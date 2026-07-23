from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot import updater


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
    ):
        updater.perform_upgrade()

    dev.assert_called_once()
    release.assert_not_called()


def test_perform_upgrade_routes_to_release_when_not_dev() -> None:
    with (
        patch.object(updater.pilot, "is_dev_build", False),
        patch.object(updater, "_upgrade_dev") as dev,
        patch.object(updater, "_upgrade_release") as release,
    ):
        updater.perform_upgrade()

    release.assert_called_once()
    dev.assert_not_called()


def _make_install(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "pilot"
    (root / "pilot").mkdir(parents=True)
    (root / "pilot" / "old.py").write_text("old")
    (root / "benches").mkdir()
    (root / "benches" / "data.txt").write_text("keep me")

    staging = root.with_name("pilot.update")
    (staging / "pilot").mkdir(parents=True)
    (staging / "pilot" / "new.py").write_text("new")
    (staging / "VERSION").write_text("v0.0.2-pre-alpha")
    return root, staging


def test_swap_in_prunes_stale_files_and_keeps_data(tmp_path: Path) -> None:
    root, staging = _make_install(tmp_path)

    updater._swap_in(root, staging, lambda _m: None)

    assert (root / "pilot" / "new.py").read_text() == "new"
    assert not (root / "pilot" / "old.py").exists()  # stale file pruned via whole-dir swap
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
