"""Tests for pilot.core.registry_cache.RegistryCache — the local git-clone
cache of the external marketplace registry, using a real local git repo as
the fake "remote" so clone/fetch/reset all run for real."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.registry_cache import RegistryCache
from pilot.exceptions import BenchError


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
    with patch("pilot.core.registry_cache.REGISTRY_URL", str(remote)), \
            patch("pilot.core.registry_cache.CronManager"):  # never touch the real system crontab
        yield remote


def make_cache(tmp_path: Path) -> RegistryCache:
    return RegistryCache(tmp_path / "cli_root")


def test_ensure_fresh_clones_on_first_use(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache.ensure_fresh()
    assert cache.apps_json_path.read_text() == '{"apps": []}'


def test_ensure_fresh_skips_network_within_refresh_window(tmp_path: Path, _point_at_local_remote) -> None:
    cache = make_cache(tmp_path)
    cache.ensure_fresh()
    commit_new_content(_point_at_local_remote, '{"apps": ["new"]}')

    with patch.object(RegistryCache, "_remote_head_sha") as mock_head:
        cache.ensure_fresh()

    mock_head.assert_not_called()
    assert cache.apps_json_path.read_text() == '{"apps": []}'  # unchanged — stale check skipped


def test_ensure_fresh_pulls_when_refresh_window_elapsed(tmp_path: Path, _point_at_local_remote) -> None:
    cache = make_cache(tmp_path)
    cache.ensure_fresh()
    commit_new_content(_point_at_local_remote, '{"apps": ["new"]}')
    _age_last_checked(cache)

    cache.ensure_fresh()

    assert cache.apps_json_path.read_text() == '{"apps": ["new"]}'


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

    assert cache.apps_json_path.read_text() == '{"apps": []}'


def test_ensure_fresh_falls_back_when_fetch_fails_mid_refresh(tmp_path: Path, _point_at_local_remote) -> None:
    """ls-remote can succeed (remote reachable) while the subsequent fetch
    still fails (connection dropped mid-transfer) — that must fall back too,
    not propagate and take down every marketplace/get-app call."""
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

    with patch.object(RegistryCache, "_remote_head_sha", return_value="deadbeef" * 5), \
            patch("pilot.core.registry_cache.run_command", side_effect=flaky_run_command):
        cache.ensure_fresh()  # must not raise

    assert cache.apps_json_path.read_text() == '{"apps": []}'


def test_ensure_fresh_raises_bench_error_when_git_status_fails(tmp_path: Path) -> None:
    """A corrupted .git dir (e.g. a partial clone) makes `git status` itself
    fail with CommandError — that must surface as a clear BenchError, not
    propagate uncaught and break every marketplace/get-app call."""
    from pilot.exceptions import CommandError

    cache = make_cache(tmp_path)
    cache.ensure_fresh()

    with patch("pilot.core.registry_cache.run_command", side_effect=CommandError("fatal: not a git repository")):
        with pytest.raises(BenchError, match="corrupted"):
            cache.ensure_fresh()


def test_ensure_fresh_falls_back_when_rev_parse_fails_mid_refresh(tmp_path: Path, _point_at_local_remote) -> None:
    """A corrupted local clone can make `git rev-parse HEAD` fail even
    though the remote is reachable — that must fall back too, not propagate."""
    from pilot.exceptions import CommandError
    from pilot.utils import run_command as real_run_command

    cache = make_cache(tmp_path)
    cache.ensure_fresh()
    _age_last_checked(cache)

    def flaky_run_command(argv, *args, **kwargs):
        if "rev-parse" in argv:
            raise CommandError("fatal: bad object HEAD")
        return real_run_command(argv, *args, **kwargs)

    with patch.object(RegistryCache, "_remote_head_sha", return_value="deadbeef" * 5), \
            patch("pilot.core.registry_cache.run_command", side_effect=flaky_run_command):
        cache.ensure_fresh()  # must not raise

    assert cache.apps_json_path.read_text() == '{"apps": []}'


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
    """Regression: an unquoted cli_root with a space breaks argv parsing for
    the cron-invoked refresh command permanently — the command string must
    survive a shell round-trip via shlex.split()."""
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
    cache.ensure_fresh()  # first clone — installs cron (via autouse-patched CronManager)

    with patch("pilot.core.registry_cache.CronManager") as mock_cron_cls:
        cache.ensure_fresh()  # already cloned, within refresh window

    mock_cron_cls.assert_not_called()


def _age_last_checked(cache: RegistryCache) -> None:
    stale = time.time() - 2 * 60 * 60
    os.utime(cache._last_checked_path, (stale, stale))
