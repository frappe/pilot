import os
from pathlib import Path

import pytest

from pilot.exceptions import CommandError
from pilot.internal.host_resources import DiskUsage


def test_disk_usage_counts_readable_parts_when_a_folder_is_locked(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads every folder")
    (tmp_path / "open").mkdir()
    (tmp_path / "open" / "data").write_bytes(b"x" * 65536)
    (tmp_path / "locked").mkdir()
    (tmp_path / "locked").chmod(0)

    try:
        size = DiskUsage(tmp_path).bytes
    finally:
        (tmp_path / "locked").chmod(0o755)

    assert size >= 65536


def test_disk_usage_raises_when_the_command_prints_nothing(tmp_path: Path) -> None:
    with pytest.raises(CommandError):
        DiskUsage(tmp_path).get_output(["sh", "-c", "echo broken >&2; exit 1"])


def test_disk_usage_of_a_missing_path_is_zero(tmp_path: Path) -> None:
    assert DiskUsage(tmp_path / "missing").bytes == 0


def test_disk_usage_measures_a_symlink_not_its_target(tmp_path: Path) -> None:
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "data").write_bytes(b"x" * 1048576)
    (tmp_path / "link").symlink_to("target")
    (tmp_path / "broken").symlink_to("missing")

    assert DiskUsage(tmp_path / "link").bytes < 65536
    assert DiskUsage(tmp_path / "broken").bytes < 65536
