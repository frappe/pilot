from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pilot.internal.atomic_file import finish_overwrite, overwrite_private_text_locked


def test_overwrite_keeps_the_inode(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"old": true}')
    inode = path.stat().st_ino

    overwrite_private_text_locked(path, '{"new": true}', tmp_path / "journal")

    assert path.stat().st_ino == inode


@pytest.mark.parametrize("existing_mode", [None, 0o644])
def test_overwrite_leaves_the_file_private(tmp_path: Path, existing_mode: int | None) -> None:
    path = tmp_path / "config.json"
    if existing_mode is not None:
        path.write_text("{}")
        path.chmod(existing_mode)

    overwrite_private_text_locked(path, "{}", tmp_path / "journal")

    assert path.stat().st_mode & 0o777 == 0o600


def test_overwrite_with_shorter_content_leaves_no_tail(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"key": "a long old value"}')

    overwrite_private_text_locked(path, '{"key": 1}', tmp_path / "journal")

    assert path.read_text() == '{"key": 1}'


def test_failed_truncate_leaves_valid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """New JSON padded with spaces still parses when the truncate step fails."""
    path = tmp_path / "config.json"
    path.write_text('{"key": "a long old value"}')

    def fail_truncate(descriptor: int, length: int) -> None:
        raise OSError("disk error")

    monkeypatch.setattr(os, "ftruncate", fail_truncate)
    with pytest.raises(OSError, match="disk error"):
        overwrite_private_text_locked(path, '{"key": 1}', tmp_path / "journal")

    assert json.loads(path.read_text()) == {"key": 1}


def test_overwrite_leaves_no_journal(tmp_path: Path) -> None:
    path = tmp_path / "config.json"

    overwrite_private_text_locked(path, "{}", tmp_path / "journal")

    assert list((tmp_path / "journal").iterdir()) == []


def test_finish_overwrite_completes_a_write_a_crash_cut_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"key": "a long old value"}')
    inode = path.stat().st_ino

    def crash(descriptor: int, data: bytes, offset: int) -> int:
        os.write(descriptor, data[:3])
        raise OSError("power lost")

    monkeypatch.setattr(os, "pwrite", crash)
    with pytest.raises(OSError, match="power lost"):
        overwrite_private_text_locked(path, '{"key": 1}', tmp_path / "journal")
    monkeypatch.undo()

    finish_overwrite(path, tmp_path / "journal")

    assert json.loads(path.read_text()) == {"key": 1}
    assert path.stat().st_ino == inode
    assert not (tmp_path / "journal" / "config.json").exists()
