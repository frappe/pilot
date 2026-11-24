import os
from pathlib import Path

import pytest

from pilot.internal.disk_scan import DiskScan
from pilot.internal.host_resources import DiskUsage


def _write_tree(root: Path) -> None:
    for folder in ("a/deep/deeper", "b", "c/inner"):
        (root / folder).mkdir(parents=True)
    (root / "a" / "deep" / "deeper" / "one.bin").write_bytes(b"1" * 40960)
    (root / "b" / "two.bin").write_bytes(b"2" * 20480)
    (root / "c" / "inner" / "three.bin").write_bytes(b"3" * 81920)
    (root / "top.bin").write_bytes(b"4" * 8192)


@pytest.mark.parametrize("pieces", [1, 3, 64])
def test_scan_matches_du_however_the_root_is_split(tmp_path: Path, pieces: int) -> None:
    _write_tree(tmp_path)
    scan = DiskScan(tmp_path)
    scan.pieces = pieces

    sizes = scan.get_sizes(lambda path: path.name in {"deep", "inner"})

    assert sizes[tmp_path] == DiskUsage(tmp_path).bytes
    assert sizes[tmp_path / "a" / "deep"] == DiskUsage(tmp_path / "a" / "deep").bytes
    assert sizes[tmp_path / "c" / "inner"] == DiskUsage(tmp_path / "c" / "inner").bytes


def test_scan_keeps_only_asked_folders_and_split_parts(tmp_path: Path) -> None:
    _write_tree(tmp_path)
    scan = DiskScan(tmp_path)
    scan.pieces = 1

    sizes = scan.get_sizes(lambda path: path.name == "inner")

    assert set(sizes) == {tmp_path, tmp_path / "c" / "inner"}


def test_scan_does_not_follow_symlinks(tmp_path: Path) -> None:
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "big.bin").write_bytes(b"x" * 1048576)
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "link").symlink_to(tmp_path / "target")

    sizes = DiskScan(tmp_path / "root").get_sizes(lambda path: False)

    assert sizes[tmp_path / "root"] < 65536
    assert DiskScan(tmp_path / "root" / "link").get_sizes(lambda path: False)[tmp_path / "root" / "link"] < 65536


def test_scan_counts_an_unreadable_folder_as_empty(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads every folder")
    _write_tree(tmp_path)
    (tmp_path / "b").chmod(0)

    try:
        sizes = DiskScan(tmp_path).get_sizes(lambda path: False)
    finally:
        (tmp_path / "b").chmod(0o755)

    assert sizes[tmp_path] >= 40960 + 81920
