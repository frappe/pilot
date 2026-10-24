"""Tests for RegistryCache using a real local git remote."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.registry_cache import RegistryCache
from pilot.exceptions import BenchError, RegistryUnavailableError


def make_remote(tmp_path: Path, content: str = '{"apps": []}') -> Path:
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-q", "-b", "main")
    _git(remote, "config", "user.email", "test@example.com")
    _git(remote, "config", "user.name", "Test")
    (remote / "apps.json").write_text(content)
    _git(remote, "add", "apps.json")
    _git(remote, "commit", "-q", "-m", "init")
    return remote


def commit_new_content(remote: Path, content: str) -> None:
    (remote / "apps.json").write_text(content)
    _git(remote, "commit", "-q", "-am", "update")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _point_at_local_remote(tmp_path):
    remote = make_remote(tmp_path)
    with (
        patch("pilot.core.registry_cache.REGISTRY_URL", str(remote)),
        patch("pilot.core.registry_cache.CronManager"),
    ):  # never touch the real system crontab
        yield remote


def make_cache(tmp_path: Path) -> RegistryCache:
    return RegistryCache(tmp_path / "cli_root")


def test_ensure_fresh_clones_on_first_use(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache.ensure_fresh()
    assert cache.index_path.read_text() == '{"apps": []}'


def test_ensure_fresh_skips_network_within_refresh_window(tmp_path: Path, _point_at_local_remote) -> None:
    cache = make_cache(tmp_path)
    cache.ensure_fresh()
    commit_new_content(_point_at_local_remote, '{"apps": ["new"]}')

    with patch.object(RegistryCache, "_remote_head_sha") as mock_head:
        cache.ensure_fresh()

    mock_head.assert_not_called()
    assert cache.index_path.read_text() == '{"apps": []}'  # unchanged - stale check skipped


def test_ensure_fresh_pulls_when_refresh_window_elapsed(tmp_path: Path, _point_at_local_remote) -> None:
    cache = make_cache(tmp_path)
    cache.ensure_fresh()
    commit_new_content(_point_at_local_remote, '{"apps": ["new"]}')
    _age_last_checked(cache)

    cache.ensure_fresh()

    assert cache.index_path.read_text() == '{"apps": ["new"]}'


def test_ensure_fresh_raises_on_manual_edit(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache.ensure_fresh()
    (cache.path / "apps.json").write_text("tampered")

    with pytest.raises(BenchError, match="modified manually"):
        cache.ensure_fresh()


def test_ensure_fresh_falls_back_to_local_clone_when_offline(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache.ensure_fresh()
    _age_last_checked(cache)

    with patch.object(RegistryCache, "_remote_head_sha", return_value=None):
        cache.ensure_fresh()  # must not raise

    assert cache.index_path.read_text() == '{"apps": []}'


def test_ensure_fresh_falls_back_when_fetch_fails_mid_refresh(tmp_path: Path, _point_at_local_remote) -> None:
    """A mid-refresh fetch failure keeps serving the local clone."""
    from pilot.exceptions import CommandError
    from pilot.utils import run_command as real_run_command

    cache = make_cache(tmp_path)
    cache.ensure_fresh()
    commit_new_content(_point_at_local_remote, '{"apps": ["new"]}')
    _age_last_checked(cache)

    def flaky_run_command(argv, *args, **kwargs):
        if "fetch" in argv:
            raise CommandError("connection reset")
        return real_run_command(argv, *args, **kwargs)

    with (
        patch.object(RegistryCache, "_remote_head_sha", return_value="deadbeef" * 5),
        patch("pilot.core.registry_cache.run_command", side_effect=flaky_run_command),
    ):
        cache.ensure_fresh()  # must not raise

    assert cache.index_path.read_text() == '{"apps": []}'


def test_ensure_fresh_raises_bench_error_when_git_status_fails(tmp_path: Path) -> None:
    """A corrupted local clone raises a clear BenchError."""
    from pilot.exceptions import CommandError

    cache = make_cache(tmp_path)
    cache.ensure_fresh()

    with (
        patch(
            "pilot.core.registry_cache.run_command",
            side_effect=CommandError("fatal: not a git repository"),
        ),
        pytest.raises(BenchError, match="corrupted"),
    ):
        cache.ensure_fresh()


def test_ensure_fresh_falls_back_when_rev_parse_fails_mid_refresh(
    tmp_path: Path, _point_at_local_remote
) -> None:
    """rev-parse failure during refresh keeps serving the local clone."""
    from pilot.exceptions import CommandError
    from pilot.utils import run_command as real_run_command

    cache = make_cache(tmp_path)
    cache.ensure_fresh()
    _age_last_checked(cache)

    def flaky_run_command(argv, *args, **kwargs):
        if "rev-parse" in argv:
            raise CommandError("fatal: bad object HEAD")
        return real_run_command(argv, *args, **kwargs)

    with (
        patch.object(RegistryCache, "_remote_head_sha", return_value="deadbeef" * 5),
        patch("pilot.core.registry_cache.run_command", side_effect=flaky_run_command),
    ):
        cache.ensure_fresh()  # must not raise

    assert cache.index_path.read_text() == '{"apps": []}'


def test_first_clone_installs_daily_refresh_cron(tmp_path: Path) -> None:
    with patch("pilot.core.registry_cache.CronManager") as mock_cron_cls:
        cache = make_cache(tmp_path)
        cache.ensure_fresh()

    mock_cron_cls.assert_called_once_with(cache._cli_root)
    mock_manager = mock_cron_cls.return_value
    mock_manager.set_schedule.assert_called_once()
    job_key, cron_expr, command = mock_manager.set_schedule.call_args.args
    assert job_key == "marketplace-registry-refresh"
    assert cron_expr == "0 3 * * *"
    assert "pilot.core.registry_cache" in command
    assert str(cache._cli_root) in command


def test_daily_refresh_cron_command_quotes_paths_with_spaces(tmp_path: Path) -> None:
    """Cron refresh command quotes cli_root paths with spaces."""
    import shlex

    spaced_root = tmp_path / "cli root with spaces"
    with patch("pilot.core.registry_cache.CronManager") as mock_cron_cls:
        cache = RegistryCache(spaced_root)
        cache.ensure_fresh()

    mock_manager = mock_cron_cls.return_value
    _, _, command = mock_manager.set_schedule.call_args.args
    argv = shlex.split(command.split(">>")[0])
    assert argv[-1] == str(spaced_root)


def test_subsequent_ensure_fresh_does_not_reinstall_cron(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache.ensure_fresh()  # first clone - installs cron (via autouse-patched CronManager)

    with patch("pilot.core.registry_cache.CronManager") as mock_cron_cls:
        cache.ensure_fresh()  # already cloned, within refresh window

    mock_cron_cls.assert_not_called()


INDEX = [{"name": "helpdesk", "repo": "https://github.com/frappe/helpdesk", "releases": "apps/helpdesk.json"}]
RELEASES = {"name": "helpdesk", "releases": [{"version": "1.27.0", "branch": "main", "commit": "a" * 40}]}


def publish(remote: Path, index: list | dict, releases: dict | None = RELEASES) -> None:
    """Write an index (and its release file) to the remote and commit it."""
    (remote / "apps.json").write_text(json.dumps(index))
    if releases is not None:
        (remote / "apps").mkdir(exist_ok=True)
        (remote / "apps" / f"{releases['name']}.json").write_text(json.dumps(releases))
    _git(remote, "add", "-A")
    _git(remote, "commit", "-q", "--allow-empty", "-m", "publish")


def test_load_returns_the_index_without_reading_release_files(
    tmp_path: Path, _point_at_local_remote
) -> None:
    publish(_point_at_local_remote, INDEX)
    cache = make_cache(tmp_path)

    entries = cache.load()

    assert entries[0]["name"] == "helpdesk"
    assert "releases" not in entries[0]


def test_releases_reads_one_apps_release_file(tmp_path: Path, _point_at_local_remote) -> None:
    publish(_point_at_local_remote, INDEX)
    cache = make_cache(tmp_path)
    cache.load()

    assert cache.releases("helpdesk") == RELEASES["releases"]


def test_releases_rejects_traversal_in_app_name(tmp_path: Path, _point_at_local_remote) -> None:
    publish(_point_at_local_remote, INDEX)
    cache = make_cache(tmp_path)
    cache.load()

    with pytest.raises(RegistryUnavailableError, match="is not a marketplace app name"):
        cache.releases("../../etc/passwd")


def test_load_rejects_releases_pointer_that_is_not_the_apps_path(
    tmp_path: Path, _point_at_local_remote
) -> None:
    index = [{**INDEX[0], "releases": "apps/other.json"}]
    publish(_point_at_local_remote, index)

    with pytest.raises(RegistryUnavailableError, match="must point 'releases' at"):
        make_cache(tmp_path).load()


def test_load_rejects_traversal_in_app_name(tmp_path: Path, _point_at_local_remote) -> None:
    index = [{"name": "../../etc/passwd", "repo": "r", "releases": "apps/../../etc/passwd.json"}]
    publish(_point_at_local_remote, index, releases=None)

    with pytest.raises(RegistryUnavailableError, match="must point 'releases' at"):
        make_cache(tmp_path).load()


def test_releases_raises_when_release_file_is_missing(tmp_path: Path, _point_at_local_remote) -> None:
    publish(_point_at_local_remote, INDEX, releases=None)
    cache = make_cache(tmp_path)
    cache.load()

    with pytest.raises(RegistryUnavailableError, match="Could not read the marketplace registry"):
        cache.releases("helpdesk")


def test_releases_raises_when_release_file_has_no_releases_list(
    tmp_path: Path, _point_at_local_remote
) -> None:
    publish(_point_at_local_remote, INDEX, releases={"name": "helpdesk"})
    cache = make_cache(tmp_path)
    cache.load()

    with pytest.raises(RegistryUnavailableError, match="does not hold a 'releases' list"):
        cache.releases("helpdesk")


def test_load_raises_when_index_is_not_a_list(tmp_path: Path, _point_at_local_remote) -> None:
    publish(_point_at_local_remote, {"apps": []}, releases=None)

    with pytest.raises(RegistryUnavailableError, match="not a list of apps"):
        make_cache(tmp_path).load()


def _head_time(remote: Path) -> datetime:
    result = subprocess.run(
        ["git", "-C", str(remote), "log", "-1", "--format=%cI"], check=True, capture_output=True, text=True
    )
    return datetime.fromisoformat(result.stdout.strip())


def test_get_added_at_returns_when_the_release_was_added(tmp_path: Path, _point_at_local_remote) -> None:
    publish(_point_at_local_remote, INDEX)
    added_at = _head_time(_point_at_local_remote)
    time.sleep(1)
    commit_new_content(_point_at_local_remote, json.dumps(INDEX, indent=2))
    cache = make_cache(tmp_path)
    cache.load()

    assert cache.get_added_at("helpdesk", "a" * 40) == added_at


def test_get_added_at_is_none_for_a_commit_never_added(tmp_path: Path, _point_at_local_remote) -> None:
    publish(_point_at_local_remote, INDEX)
    cache = make_cache(tmp_path)
    cache.load()

    assert cache.get_added_at("helpdesk", "b" * 40) is None


def test_refresh_turns_a_shallow_clone_into_a_full_one(tmp_path: Path, _point_at_local_remote) -> None:
    publish(_point_at_local_remote, INDEX)
    commit_new_content(_point_at_local_remote, json.dumps(INDEX, indent=2))
    cache = make_cache(tmp_path)
    # Older Pilots cloned with --depth 1. A file:// URL makes git honour --depth locally.
    _git(tmp_path, "clone", "-q", "--depth", "1", f"file://{_point_at_local_remote}", str(cache.path))
    assert cache.get_added_at("helpdesk", "a" * 40) is None

    cache.ensure_fresh()

    assert cache.get_added_at("helpdesk", "a" * 40) is not None


def _age_last_checked(cache: RegistryCache) -> None:
    stale = time.time() - 2 * 60 * 60
    os.utime(cache._last_checked_path, (stale, stale))
