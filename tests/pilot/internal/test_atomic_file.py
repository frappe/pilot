from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pilot.internal.atomic_file import overwrite_private_text_locked


def test_overwrite_keeps_the_inode(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"old": true}')
    inode = path.stat().st_ino

    overwrite_private_text_locked(path, '{"new": true}')

    assert path.stat().st_ino == inode


@pytest.mark.parametrize("existing_mode", [None, 0o644])
def test_overwrite_leaves_the_file_private(tmp_path: Path, existing_mode: int | None) -> None:
    path = tmp_path / "config.json"
    if existing_mode is not None:
        path.write_text("{}")
        path.chmod(existing_mode)

    overwrite_private_text_locked(path, "{}")

    assert path.stat().st_mode & 0o777 == 0o600


def test_overwrite_with_shorter_content_leaves_no_tail(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"key": "a long old value"}')

    overwrite_private_text_locked(path, '{"key": 1}')

    assert path.read_text() == '{"key": 1}'


def test_failed_truncate_leaves_valid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """New JSON padded with spaces still parses when the truncate step fails."""
    path = tmp_path / "config.json"
    path.write_text('{"key": "a long old value"}')

    def fail_truncate(descriptor: int, length: int) -> None:
        raise OSError("disk error")

    monkeypatch.setattr(os, "ftruncate", fail_truncate)
    with pytest.raises(OSError, match="disk error"):
        overwrite_private_text_locked(path, '{"key": 1}')

    assert json.loads(path.read_text()) == {"key": 1}
