from __future__ import annotations

from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from pilot import updater
from pilot.exceptions import BenchError


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


def _patch_git_state(branch: str, tag_at_head: str = ""):
    return (
        patch.object(updater.GitRepo, "branch", new_callable=PropertyMock, return_value=branch),
        patch.object(updater.GitRepo, "tag_at_head", new_callable=PropertyMock, return_value=tag_at_head),
        patch.object(updater.GitRepo, "fetch", return_value=True),
    )


def test_upgrade_dev_pulls_when_on_a_branch() -> None:
    branch, tag, fetch = _patch_git_state("develop")
    with (
        patch.object(updater, "cli_root", return_value=Path("/opt/pilot")),
        branch, tag, fetch,
        patch("pilot.utils.run_command") as run,
        patch.object(updater, "_rebuild_dev_install") as rebuild,
    ):
        updater._upgrade_dev(lambda _m: None)

    assert run.call_args.args[0] == ["git", "-C", "/opt/pilot", "pull"]
    rebuild.assert_called_once()


def test_upgrade_dev_moves_tag_pinned_checkout_to_latest_release() -> None:
    branch, tag, fetch = _patch_git_state("", tag_at_head="v0.0.1-pre-alpha")
    with (
        patch.object(updater, "cli_root", return_value=Path("/opt/pilot")),
        branch, tag, fetch as fetched,
        patch.object(updater, "latest_release", return_value={"tag": "v0.0.2-pre-alpha", "asset_url": None}),
        patch("pilot.utils.run_command") as run,
        patch.object(updater, "_rebuild_dev_install") as rebuild,
    ):
        updater._upgrade_dev(lambda _m: None)

    fetched.assert_called_once_with("--tags")
    assert run.call_args.args[0] == ["git", "-C", "/opt/pilot", "checkout", "v0.0.2-pre-alpha"]
    rebuild.assert_called_once()


def test_upgrade_dev_skips_rebuild_when_already_on_latest_tag() -> None:
    branch, tag, fetch = _patch_git_state("", tag_at_head="v0.0.2-pre-alpha")
    messages: list[str] = []
    with (
        patch.object(updater, "cli_root", return_value=Path("/opt/pilot")),
        branch, tag, fetch,
        patch.object(updater, "latest_release", return_value={"tag": "v0.0.2-pre-alpha", "asset_url": None}),
        patch.object(updater, "_rebuild_dev_install") as rebuild,
    ):
        updater._upgrade_dev(messages.append)

    rebuild.assert_not_called()
    assert messages == ["Already on the latest version (v0.0.2-pre-alpha)."]


def test_upgrade_dev_rejects_a_detached_commit() -> None:
    branch, tag, fetch = _patch_git_state("")
    with (
        patch.object(updater, "cli_root", return_value=Path("/opt/pilot")),
        branch,
        tag,
        fetch,
        patch.object(updater, "latest_release") as latest,
        patch.object(updater, "_rebuild_dev_install") as rebuild,
        pytest.raises(BenchError, match="not on a release tag"),
    ):
        updater._upgrade_dev(lambda _m: None)

    latest.assert_not_called()
    rebuild.assert_not_called()


def test_upgrade_dev_fails_when_tag_cannot_be_fetched() -> None:
    branch, tag, _fetch = _patch_git_state("", tag_at_head="v0.0.1-pre-alpha")
    with (
        patch.object(updater, "cli_root", return_value=Path("/opt/pilot")),
        branch, tag,
        patch.object(updater.GitRepo, "fetch", return_value=False),
        patch.object(updater.GitRepo, "has_commit", return_value=False),
        patch.object(updater, "latest_release", return_value={"tag": "v0.0.2-pre-alpha", "asset_url": None}),
        patch.object(updater, "_rebuild_dev_install") as rebuild,
        pytest.raises(BenchError, match="Could not fetch"),
    ):
        updater._upgrade_dev(lambda _m: None)

    rebuild.assert_not_called()


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
