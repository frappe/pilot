from __future__ import annotations

import io
import stat
import tarfile
import urllib.request
from pathlib import Path

import pytest

from pilot.utils import (
    ArchiveLimits,
    UnsafeArchiveError,
    extract_tar_archive,
    validate_tar_archive,
)


def _archive(path: Path, members: list[tuple[tarfile.TarInfo, bytes]]) -> Path:
    with tarfile.open(path, "w") as archive:
        for member, content in members:
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content) if member.isfile() else None)
    return path


def _file(name: str, content: bytes = b"content") -> tuple[tarfile.TarInfo, bytes]:
    return tarfile.TarInfo(name), content


@pytest.mark.parametrize("name", ["../escape.txt", "nested/../../escape.txt", "/escape.txt"])
def test_validate_rejects_paths_outside_destination(tmp_path: Path, name: str) -> None:
    path = _archive(tmp_path / "unsafe.tar", [_file(name)])

    with pytest.raises(UnsafeArchiveError, match="unsafe path"):
        validate_tar_archive(path)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_validate_rejects_links(tmp_path: Path, kind: bytes) -> None:
    link = tarfile.TarInfo("link")
    link.type = kind
    link.linkname = "target"
    path = _archive(tmp_path / "link.tar", [(link, b"")])

    with pytest.raises(UnsafeArchiveError, match="links"):
        validate_tar_archive(path)


def test_validate_rejects_special_files(tmp_path: Path) -> None:
    fifo = tarfile.TarInfo("pipe")
    fifo.type = tarfile.FIFOTYPE
    path = _archive(tmp_path / "special.tar", [(fifo, b"")])

    with pytest.raises(UnsafeArchiveError, match="regular files and directories"):
        validate_tar_archive(path)


def test_validate_rejects_oversized_member(tmp_path: Path) -> None:
    path = _archive(tmp_path / "large.tar", [_file("large.bin", b"12345")])

    with pytest.raises(UnsafeArchiveError, match="member size"):
        validate_tar_archive(path, ArchiveLimits(max_member_bytes=4))


def test_validate_rejects_oversized_expansion(tmp_path: Path) -> None:
    path = _archive(
        tmp_path / "large.tar",
        [_file("one.bin", b"123"), _file("two.bin", b"456")],
    )

    with pytest.raises(UnsafeArchiveError, match="expanded size"):
        validate_tar_archive(path, ArchiveLimits(max_total_bytes=5))


def test_validate_rejects_too_many_members(tmp_path: Path) -> None:
    path = _archive(tmp_path / "many.tar", [_file("one"), _file("two")])

    with pytest.raises(UnsafeArchiveError, match="member count"):
        validate_tar_archive(path, ArchiveLimits(max_members=1))


def test_extract_writes_regular_files_inside_destination(tmp_path: Path) -> None:
    path = _archive(tmp_path / "safe.tar", [_file("dist/app.js", b"app")])
    destination = tmp_path / "static"

    extract_tar_archive(path, destination)

    assert (destination / "dist" / "app.js").read_bytes() == b"app"


def test_extract_does_not_apply_root_entry_mode_to_destination(tmp_path: Path) -> None:
    root = tarfile.TarInfo(".")
    root.type = tarfile.DIRTYPE
    root.mode = 0
    path = _archive(tmp_path / "root.tar", [(root, b""), _file("app.js", b"app")])
    destination = tmp_path / "static"
    destination.mkdir(mode=0o700)

    extract_tar_archive(path, destination)

    assert stat.S_IMODE(destination.stat().st_mode) == 0o700


def test_extract_rejects_existing_symlink_parent(tmp_path: Path) -> None:
    path = _archive(tmp_path / "safe-name.tar", [_file("linked/escape.txt")])
    destination = tmp_path / "static"
    outside = tmp_path / "outside"
    destination.mkdir()
    outside.mkdir()
    (destination / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeArchiveError, match="symlink"):
        extract_tar_archive(path, destination)

    assert not (outside / "escape.txt").exists()


def test_admin_frontend_download_rejects_unsafe_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pilot.commands.admin.start import download_admin_frontend

    archive = _archive(tmp_path / "admin.tar", [_file("../../escape.txt")])

    def retrieve(_url: str, destination: str | Path):
        Path(destination).write_bytes(archive.read_bytes())
        return str(destination), None

    monkeypatch.setattr(urllib.request, "urlretrieve", retrieve)
    cli_root = tmp_path / "cli"

    assert download_admin_frontend(cli_root) is False
    assert not (cli_root / "admin" / "escape.txt").exists()


def test_prebuilt_asset_download_rejects_unsafe_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pilot.managers.python_environment import PythonEnvManager

    archive = _archive(tmp_path / "assets.tar", [_file("../escape.txt")])

    def retrieve(_url: str, destination: str | Path):
        Path(destination).write_bytes(archive.read_bytes())
        return str(destination), None

    monkeypatch.setattr(urllib.request, "urlretrieve", retrieve)

    assert (
        PythonEnvManager._download_and_extract(
            "https://example.test/assets.tar", tmp_path / "public"
        )
        is False
    )
    assert not (tmp_path / "escape.txt").exists()
