from __future__ import annotations

import os
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pilot.exceptions import BenchError


@dataclass(frozen=True)
class ArchiveLimits:
    max_members: int = 100_000
    max_member_bytes: int = 8 * 1024**3
    max_total_bytes: int = 32 * 1024**3


class UnsafeArchiveError(BenchError):
    pass


def validate_tar_archive(path: Path, limits: ArchiveLimits = ArchiveLimits()) -> None:
    try:
        with tarfile.open(path) as archive:
            _validated_members(archive, limits)
    except (OSError, tarfile.TarError) as exc:
        raise UnsafeArchiveError("File is not a readable tar archive.") from exc


def extract_tar_archive(
    path: Path,
    destination: Path,
    limits: ArchiveLimits = ArchiveLimits(),
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    try:
        with tarfile.open(path) as archive:
            members = _validated_members(archive, limits)
            targets = [(member, _safe_target(root, member.name)) for member in members]
            for member, target in targets:
                _reject_symlink_path(root, target)
                if target.exists() and member.isdir() != target.is_dir():
                    raise UnsafeArchiveError(f"Archive member conflicts with {target}.")

            directories = [
                (member, target) for member, target in targets if member.isdir() and target != root
            ]
            for _, target in directories:
                target.mkdir(parents=True, exist_ok=True)
            for member, target in targets:
                if member.isfile():
                    _write_member(archive, member, root, target)
            for member, target in reversed(directories):
                target.chmod(member.mode & 0o777)
    except (OSError, tarfile.TarError) as exc:
        raise UnsafeArchiveError(f"Archive extraction failed: {exc}") from exc


def _validated_members(
    archive: tarfile.TarFile,
    limits: ArchiveLimits,
) -> list[tarfile.TarInfo]:
    members = []
    paths: dict[tuple[str, ...], bool] = {}
    total_size = 0
    for index, member in enumerate(archive, start=1):
        if index > limits.max_members:
            raise UnsafeArchiveError("Archive exceeds the member count limit.")

        parts = _safe_parts(member.name)
        if member.issym() or member.islnk():
            raise UnsafeArchiveError(f"Archive links are not allowed: {member.name}")
        if not member.isfile() and not member.isdir():
            raise UnsafeArchiveError("Archive may contain only regular files and directories.")
        if member.size > limits.max_member_bytes:
            raise UnsafeArchiveError(f"Archive member size exceeds the limit: {member.name}")

        total_size += member.size
        if total_size > limits.max_total_bytes:
            raise UnsafeArchiveError("Archive expanded size exceeds the limit.")
        _check_path_conflict(paths, parts, member.isdir())
        paths[parts] = member.isdir()
        members.append(member)
    return members


def _safe_parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\0" in name:
        raise UnsafeArchiveError(f"Archive contains an unsafe path: {name}")
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts and name not in (".", "./"):
        raise UnsafeArchiveError(f"Archive contains an unsafe path: {name}")
    return parts


def _check_path_conflict(
    paths: dict[tuple[str, ...], bool],
    parts: tuple[str, ...],
    is_directory: bool,
) -> None:
    if parts in paths:
        raise UnsafeArchiveError(f"Archive contains a duplicate path: {'/'.join(parts)}")
    if any(paths.get(parts[:index]) is False for index in range(1, len(parts))):
        raise UnsafeArchiveError(f"Archive path crosses a regular file: {'/'.join(parts)}")
    if not is_directory and any(path[: len(parts)] == parts for path in paths):
        raise UnsafeArchiveError(f"Archive path conflicts with a directory: {'/'.join(parts)}")


def _safe_target(root: Path, name: str) -> Path:
    target = root.joinpath(*_safe_parts(name))
    _reject_symlink_path(root, target)
    target = target.resolve(strict=False)
    if not target.is_relative_to(root):
        raise UnsafeArchiveError(f"Archive contains an unsafe path: {name}")
    return target


def _reject_symlink_path(root: Path, target: Path) -> None:
    current = root
    for part in target.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise UnsafeArchiveError(f"Archive target crosses a symlink: {current}")


def _write_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    root: Path,
    target: Path,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_path(root, target)
    if target.exists():
        target.unlink()

    source = archive.extractfile(member)
    if source is None:
        raise UnsafeArchiveError(f"Could not read archive member: {member.name}")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            remaining = member.size
            while remaining:
                chunk = source.read(min(1024**2, remaining))
                if not chunk:
                    raise UnsafeArchiveError(f"Archive member is truncated: {member.name}")
                output.write(chunk)
                remaining -= len(chunk)
        target.chmod(member.mode & 0o777)
    except Exception:
        target.unlink(missing_ok=True)
        raise
