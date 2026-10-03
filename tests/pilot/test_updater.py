from __future__ import annotations

import urllib.error
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot import updater
from pilot.exceptions import BenchError, ConfigError


def test_update_available_true_when_tag_differs() -> None:
    with (
        patch.object(updater.pilot, "__version__", "v0.0.1-pre-alpha"),
        patch.object(
            updater, "get_target_release", return_value={"tag": "v0.0.2-pre-alpha", "asset_url": "x"}
        ),
    ):
        available, latest = updater.update_available()

    assert available is True
    assert latest == "v0.0.2-pre-alpha"


def test_update_available_false_on_same_tag() -> None:
    with (
        patch.object(updater.pilot, "__version__", "v0.0.2-pre-alpha"),
        patch.object(
            updater, "get_target_release", return_value={"tag": "v0.0.2-pre-alpha", "asset_url": "x"}
        ),
    ):
        available, latest = updater.update_available()

    assert available is False
    assert latest == "v0.0.2-pre-alpha"


def test_update_available_false_when_no_release() -> None:
    with patch.object(updater, "get_target_release", return_value=None):
        available, latest = updater.update_available()

    assert available is False
    assert latest is None


@contextmanager
def _rollout(answer=None, *, central_down=False, central_enabled=True, channel="early"):
    """Central answers `answer` for this early-channel host; yields the Central call mock."""
    from pilot.config.central import CentralConfig
    from pilot.config.common import CommonConfig
    from pilot.integrations.central.client import CentralClient, CentralClientError

    common = CommonConfig(central=CentralConfig(enabled=central_enabled, update_channel=channel))
    with (
        patch.object(CommonConfig, "read", return_value=common),
        patch.object(
            CentralClient,
            "get_pilot_release",
            return_value=answer,
            side_effect=CentralClientError("down") if central_down else None,
        ) as ask,
        patch.object(updater, "latest_release", return_value={"tag": "newest"}),
        patch.object(updater, "get_release", side_effect=lambda tag: {"tag": tag}),
    ):
        yield ask


def test_target_release_is_centrals_tag_when_allowed() -> None:
    with _rollout({"tag": "v1", "allowed": True}) as ask:
        assert updater.get_target_release() == {"tag": "v1"}
    ask.assert_called_once_with("early")


def test_target_release_is_none_when_rollout_holds_this_host_back() -> None:
    with _rollout({"tag": "v1", "allowed": False}):
        assert updater.get_target_release() is None


@pytest.mark.parametrize(
    "rollout",
    [
        {"answer": {"tag": None}},
        {"central_enabled": False},
    ],
)
def test_target_release_falls_back_to_newest(rollout: dict) -> None:
    with _rollout(**rollout):
        assert updater.get_target_release() == {"tag": "newest"}


def test_a_host_that_cannot_reach_central_does_not_update() -> None:
    from pilot.integrations.central.client import CentralClientError

    with _rollout(central_down=True), pytest.raises(CentralClientError):
        updater.get_target_release()


def test_upgrade_release_does_nothing_when_rollout_holds_this_host_back() -> None:
    messages: list[str] = []
    with patch.object(updater, "get_target_release", return_value=None):
        updater._upgrade_release(messages.append)
    assert messages == [f"No update available for this server yet ({updater.pilot.__version__})."]


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v0.0.53-pre-alpha", True),
        ("v0.0.52-pre-alpha", False),
        ("v0.0.51-pre-alpha", False),
        ("v0.1.0", True),
    ],
)
def test_only_a_later_release_counts_as_an_update(tag: str, expected: bool) -> None:
    with patch.object(updater.pilot, "__version__", "v0.0.52-pre-alpha"):
        assert updater.is_newer(tag) is expected


def test_upgrade_release_never_goes_back_to_an_older_tag() -> None:
    messages: list[str] = []
    with (
        patch.object(updater.pilot, "__version__", "v0.0.52-pre-alpha"),
        patch.object(
            updater, "get_target_release", return_value={"tag": "v0.0.51-pre-alpha", "asset_url": "x"}
        ),
        patch.object(updater, "_reset_dir") as install,
    ):
        updater._upgrade_release(messages.append)

    install.assert_not_called()
    assert messages == ["Already on the latest version (v0.0.52-pre-alpha)."]


def test_a_tag_github_does_not_have_is_named_in_the_error() -> None:
    missing = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
    with (
        patch.object(updater, "_get_github_json", side_effect=missing),
        pytest.raises(BenchError, match="GitHub has no such release"),
    ):
        updater.get_release("v9.9.9")


def test_a_bad_update_channel_fails_loudly() -> None:
    with (
        _rollout({"tag": "v1", "allowed": True}, channel="Early") as ask,
        pytest.raises(ConfigError, match="must be one of early, normal, late"),
    ):
        updater.get_target_release()
    ask.assert_not_called()


@contextmanager
def _upgrade_to_v2(upgrade=None, patches=None, dev=False):
    """perform_upgrade from v1 to v2, with any step swapped for a mock failure."""
    with (
        patch.object(updater.pilot, "__version__", "v1"),
        patch.object(updater.pilot, "is_dev_build", dev),
        patch.object(updater, "_upgrade_dev"),
        patch.object(updater, "_upgrade_release", return_value="v2", side_effect=upgrade),
        patch("pilot.internal.patch_runner.run_patches", side_effect=patches),
        patch.object(updater, "_report_update") as reported,
    ):
        yield reported


def test_a_finished_update_reports_the_new_version() -> None:
    with _upgrade_to_v2() as reported:
        updater.perform_upgrade()
    reported.assert_called_once_with("v2")


@pytest.mark.parametrize(
    "failure",
    [
        {"upgrade": RuntimeError("disk full")},
        {"upgrade": BenchError("GitHub has no such release: Pilot v2.")},
        {"patches": RuntimeError("patch broke")},
    ],
    ids=["install", "bad tag", "patch"],
)
def test_any_failed_step_reports_why_and_still_raises(failure: dict) -> None:
    (error,) = failure.values()
    with _upgrade_to_v2(**failure) as reported, pytest.raises(type(error)):
        updater.perform_upgrade()
    reported.assert_called_once_with("v1", f"Pilot update failed: {error}")


def test_a_held_back_host_reports_nothing() -> None:
    with _upgrade_to_v2(upgrade=lambda on_progress: None) as reported:
        updater.perform_upgrade()
    reported.assert_not_called()


def test_a_dev_install_never_reports() -> None:
    with _upgrade_to_v2(dev=True) as reported:
        updater.perform_upgrade()
    reported.assert_not_called()


def test_perform_upgrade_routes_to_dev_when_dev_build() -> None:
    with (
        patch.object(updater.pilot, "is_dev_build", True),
        patch.object(updater, "_upgrade_dev") as dev,
        patch.object(updater, "_upgrade_release") as release,
        patch.object(updater, "_report_update"),
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
        patch.object(updater, "_report_update"),
        patch("pilot.internal.patch_runner.run_patches"),
    ):
        updater.perform_upgrade()

    release.assert_called_once()
    dev.assert_not_called()


def test_perform_upgrade_runs_pre_and_post_update_patches_in_order() -> None:
    calls: list[str] = []
    with (
        patch.object(updater.pilot, "is_dev_build", True),
        patch.object(updater, "_upgrade_dev", side_effect=lambda _on_progress: calls.append("upgrade")),
        patch(
            "pilot.internal.patch_runner.run_patches",
            side_effect=lambda phase, on_progress=None: calls.append(phase),
        ),
    ):
        updater.perform_upgrade()

    assert calls == ["pre_update", "upgrade", "post_update"]


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
