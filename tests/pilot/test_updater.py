from __future__ import annotations

import io
import subprocess
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from pilot import updater
from pilot.core.server import Server
from pilot.core.server.monitoring_config import MonitorConfigurator
from pilot.exceptions import CommandError


@pytest.fixture(autouse=True)
def _keep_the_host_monitor_running(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    restarts: list[str] = []
    monkeypatch.setattr(MonitorConfigurator, "restart", lambda self: restarts.append("restart monitor"))
    monkeypatch.setattr(Server, "is_database_server", False)
    return restarts


FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>tag:github.com,2008:Repository/1/v0.0.103-pre-alpha</id>
    <link rel="alternate" type="text/html" href="https://github.com/frappe/pilot/releases/tag/v0.0.103-pre-alpha"/>
    <title>Release name differs from tag</title>
  </entry>
  <entry>
    <link rel="alternate" type="text/html" href="https://github.com/frappe/pilot/releases/tag/v0.0.102-pre-alpha"/>
  </entry>
</feed>"""


def test_latest_release_reads_the_newest_entry_of_the_public_feed() -> None:
    with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(FEED)) as urlopen:
        release = updater.latest_release()

    assert urlopen.call_args.args[0].full_url == "https://github.com/frappe/pilot/releases.atom"
    assert release == {
        "tag": "v0.0.103-pre-alpha",
        "asset_url": "https://github.com/frappe/pilot/releases/download/v0.0.103-pre-alpha/pilot.tar.gz",
    }


def test_latest_release_is_none_for_an_empty_feed() -> None:
    feed = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(feed)):
        assert updater.latest_release() is None


def test_update_available_true_when_tag_differs() -> None:
    with (
        patch.object(updater.pilot, "__version__", "v0.0.1-pre-alpha"),
        patch.object(updater, "latest_release", return_value={"tag": "v0.0.2-pre-alpha", "asset_url": "x"}),
    ):
        available, latest = updater.update_available()

    assert available is True
    assert latest == "v0.0.2-pre-alpha"


def test_update_available_false_on_same_tag() -> None:
    with (
        patch.object(updater.pilot, "__version__", "v0.0.2-pre-alpha"),
        patch.object(updater, "latest_release", return_value={"tag": "v0.0.2-pre-alpha", "asset_url": "x"}),
    ):
        available, latest = updater.update_available()

    assert available is False
    assert latest == "v0.0.2-pre-alpha"


def test_update_available_false_when_no_release() -> None:
    with patch.object(updater, "latest_release", return_value=None):
        available, latest = updater.update_available()

    assert available is False
    assert latest is None


def test_perform_upgrade_routes_to_dev_when_dev_build() -> None:
    with (
        patch.object(updater.pilot, "is_dev_build", True),
        patch.object(updater, "_upgrade_dev") as dev,
        patch.object(updater, "_upgrade_release") as release,
        patch("pilot.internal.patch_runner.run_patches"),
    ):
        updater.perform_upgrade()

    dev.assert_called_once()
    release.assert_not_called()


def test_perform_upgrade_routes_to_release_when_not_dev() -> None:
    with (
        patch.object(updater.pilot, "is_dev_build", False),
        patch.object(updater, "_upgrade_dev") as dev,
        patch.object(updater, "_upgrade_release") as release,
        patch("pilot.internal.patch_runner.run_patches"),
    ):
        updater.perform_upgrade()

    release.assert_called_once()
    dev.assert_not_called()


def test_perform_upgrade_runs_patches_in_order_then_restarts_the_monitor(
    _keep_the_host_monitor_running: list[str],
) -> None:
    calls = _keep_the_host_monitor_running
    with (
        patch.object(updater.pilot, "is_dev_build", True),
        patch.object(
            updater, "_upgrade_dev", side_effect=lambda _on_progress, has_admin: calls.append("upgrade")
        ),
        patch(
            "pilot.internal.patch_runner.run_patches",
            side_effect=lambda phase, on_progress=None: calls.append(phase),
        ),
    ):
        updater.perform_upgrade()

    assert calls == ["pre_update", "upgrade", "post_update", "restart monitor"]


def test_a_database_server_upgrade_builds_no_admin_and_restarts_the_agent_with_new_code(
    _keep_the_host_monitor_running: list[str],
) -> None:
    calls = _keep_the_host_monitor_running
    with (
        patch.object(Server, "is_database_server", new_callable=PropertyMock, return_value=True),
        patch.object(updater.pilot, "is_dev_build", True),
        patch("pilot.internal.command.run_command", side_effect=lambda argv, **_: calls.append(argv[-1])),
        patch(
            "pilot.managers.environment.AdminEnvManager.install_python_deps",
            lambda self: calls.append("deps"),
        ),
        patch("admin.backend.frontend.ensure_admin_frontend") as build_frontend,
        patch(
            "pilot.internal.patch_runner.run_patches",
            side_effect=lambda phase, on_progress: calls.append(phase),
        ),
        patch.object(updater.subprocess, "run", side_effect=lambda argv, **_: calls.append(argv[1:])),
    ):
        updater.perform_upgrade()

    build_frontend.assert_not_called()
    assert calls == [
        "pre_update",
        "pull",
        "deps",
        "post_update",
        "restart monitor",
        ["-P", "-m", "pilot.core.database.agent.setup"],
    ]


def test_a_failed_dev_pull_names_the_reason_git_gave(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    missing = str(tmp_path / "missing.git")
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", missing], check=True)

    with (
        patch.object(Server, "root", new_callable=PropertyMock, return_value=tmp_path),
        pytest.raises(CommandError, match="does not appear to be a git repository"),
    ):
        updater._upgrade_dev(lambda message: None, has_admin=False)


def test_perform_upgrade_skips_post_update_patches_when_upgrade_fails() -> None:
    with (
        patch.object(updater.pilot, "is_dev_build", True),
        patch.object(updater, "_upgrade_dev", side_effect=RuntimeError("boom")),
        patch("pilot.internal.patch_runner.run_patches") as run_patches,
        pytest.raises(RuntimeError, match="boom"),
    ):
        updater.perform_upgrade()

    assert run_patches.call_count == 1
    assert run_patches.call_args.args[0] == "pre_update"


def _make_install(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "pilot"
    (root / "pilot").mkdir(parents=True)
    (root / "pilot" / "old.py").write_text("old")
    (root / "bench").write_text("old launcher")
    (root / "benches").mkdir()
    (root / "benches" / "data.txt").write_text("keep me")

    staging = root.with_name("pilot.update")
    (staging / "pilot").mkdir(parents=True)
    (staging / "pilot" / "new.py").write_text("new")
    (staging / "bin").mkdir()
    (staging / "bin" / "pilot").write_text("new launcher")
    (staging / "VERSION").write_text("v0.0.2-pre-alpha")
    return root, staging


def test_swap_in_prunes_stale_files_and_keeps_data(tmp_path: Path) -> None:
    root, staging = _make_install(tmp_path)

    updater._swap_in(root, staging, lambda _m: None)

    assert (root / "pilot" / "new.py").read_text() == "new"
    assert not (root / "pilot" / "old.py").exists()  # stale file pruned via whole-dir swap
    assert (root / "bin" / "pilot").read_text() == "new launcher"
    assert not (root / "bench").exists()
    assert (root / "VERSION").read_text() == "v0.0.2-pre-alpha"
    assert (root / "benches" / "data.txt").read_text() == "keep me"  # data untouched
    assert not root.with_name("pilot.backup").exists()  # backup cleaned up


def test_swap_in_rolls_back_on_failure(tmp_path: Path) -> None:
    root, staging = _make_install(tmp_path)

    real_rename = updater.os.rename

    def flaky_rename(src, dst):
        if Path(src).name == "pilot" and Path(src).parent == staging:
            raise OSError("boom")
        return real_rename(src, dst)

    with patch.object(updater.os, "rename", flaky_rename), pytest.raises(OSError):
        updater._swap_in(root, staging, lambda _m: None)

    assert (root / "pilot" / "old.py").read_text() == "old"
    assert not (root / "pilot" / "new.py").exists()
    assert (root / "bench").read_text() == "old launcher"
    assert not root.with_name("pilot.backup").exists()
    assert (root / "benches" / "data.txt").read_text() == "keep me"


def test_swap_in_keeps_backup_when_rollback_fails(tmp_path: Path) -> None:
    root, staging = _make_install(tmp_path)
    backup = root.with_name("pilot.backup")

    real_rename = updater.os.rename

    def flaky_rename(src, dst):
        src_path = Path(src)
        if src_path.name == "pilot" and src_path.parent in (staging, backup):
            raise OSError("boom")
        return real_rename(src, dst)

    with patch.object(updater.os, "rename", flaky_rename), pytest.raises(OSError):
        updater._swap_in(root, staging, lambda _m: None)

    assert (backup / "pilot" / "old.py").read_text() == "old"
