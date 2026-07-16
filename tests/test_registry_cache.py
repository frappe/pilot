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
    (remote / "apps_v2.json").write_text(content)
    _git(remote, "add", "apps_v2.json")
    _git(remote, "commit", "-q", "-m", "init")
    return remote


def commit_new_content(remote: Path, content: str) -> None:
    (remote / "apps_v2.json").write_text(content)
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
    assert cache.apps_v2_path.read_text() == '{"apps": []}'


def test_ensure_fresh_skips_network_within_refresh_window(tmp_path: Path, _point_at_local_remote) -> None:
    cache = make_cache(tmp_path)
    cache.ensure_fresh()
    commit_new_content(_point_at_local_remote, '{"apps": ["new"]}')

    with patch.object(RegistryCache, "_remote_head_sha") as mock_head:
        cache.ensure_fresh()

    mock_head.assert_not_called()
    assert cache.apps_v2_path.read_text() == '{"apps": []}'  # unchanged — stale check skipped


def test_ensure_fresh_pulls_when_refresh_window_elapsed(tmp_path: Path, _point_at_local_remote) -> None:
    cache = make_cache(tmp_path)
    cache.ensure_fresh()
    commit_new_content(_point_at_local_remote, '{"apps": ["new"]}')
    _age_last_checked(cache)

    cache.ensure_fresh()

    assert cache.apps_v2_path.read_text() == '{"apps": ["new"]}'


def test_ensure_fresh_raises_on_manual_edit(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache.ensure_fresh()
    (cache.path / "apps_v2.json").write_text("tampered")

    with pytest.raises(BenchError, match="modified manually"):
        cache.ensure_fresh()


def test_ensure_fresh_falls_back_to_local_clone_when_offline(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache.ensure_fresh()
    _age_last_checked(cache)

    with patch.object(RegistryCache, "_remote_head_sha", return_value=None):
        cache.ensure_fresh()  # must not raise

    assert cache.apps_v2_path.read_text() == '{"apps": []}'


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


def test_subsequent_ensure_fresh_does_not_reinstall_cron(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache.ensure_fresh()  # first clone — installs cron (via autouse-patched CronManager)

    with patch("pilot.core.registry_cache.CronManager") as mock_cron_cls:
        cache.ensure_fresh()  # already cloned, within refresh window

    mock_cron_cls.assert_not_called()


def _age_last_checked(cache: RegistryCache) -> None:
    stale = time.time() - 2 * 60 * 60
    os.utime(cache._last_checked_path, (stale, stale))
