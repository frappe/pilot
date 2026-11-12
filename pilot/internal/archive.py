from __future__ import annotations

import os
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pilot.exceptions import BenchError


class UnsafeArchiveError(BenchError):
    """An archive that is unreadable, too large, or would write outside its destination."""


@dataclass(frozen=True)
class ArchiveLimits:
    """Upper bounds that stop a hostile archive from filling the disk."""

    max_members: int = 100_000
    max_member_bytes: int = 8 * 1024**3
    max_total_bytes: int = 32 * 1024**3


class TarArchive:
    """A tar archive from outside Pilot, checked before anything is written.

    Only regular files and directories are allowed. Links, absolute paths, `..` and paths
    that cross a symbolic link in the destination are refused."""

    def __init__(self, path: Path, limits: ArchiveLimits | None = None) -> None:
        self.path = path
        self.limits = limits or ArchiveLimits()

    def validate(self) -> None:
        """Raise UnsafeArchiveError when the archive would be refused by extract_to()."""
        try:
            with tarfile.open(self.path) as archive:
                self.get_checked_members(archive)
        except (OSError, tarfile.TarError) as exc:
            raise UnsafeArchiveError("File is not a readable tar archive.") from exc

    def extract_to(self, destination: Path) -> None:
        """Extract every member under destination, with each file created fresh."""
        destination.mkdir(parents=True, exist_ok=True)
        root = destination.resolve()
        try:
            with tarfile.open(self.path) as archive:
                members = self.get_checked_members(archive)
                targets = [(member, self.get_target(root, member.name)) for member in members]
                for member, target in targets:
                    self.reject_symlink_in_path(root, target)
                    if target.exists() and member.isdir() != target.is_dir():
                        raise UnsafeArchiveError(f"Archive member conflicts with {target}.")
                directories = [
                    (member, target) for member, target in targets if member.isdir() and target != root
                ]
                for _, target in directories:
                    target.mkdir(parents=True, exist_ok=True)
                for member, target in targets:
                    if member.isfile():
                        self.write_member(archive, member, root, target)
                for member, target in reversed(directories):
                    target.chmod(member.mode & 0o777)
        except (OSError, tarfile.TarError) as exc:
            raise UnsafeArchiveError(f"Archive extraction failed: {exc}") from exc

    def get_checked_members(self, archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
        """Every member, after the type, size, count and path checks."""
        members = []
        paths: dict[tuple[str, ...], bool] = {}
        total_size = 0
        for index, member in enumerate(archive, start=1):
            if index > self.limits.max_members:
                raise UnsafeArchiveError("Archive exceeds the member count limit.")
            parts = self.get_safe_parts(member.name)
            if member.issym() or member.islnk():
                raise UnsafeArchiveError(f"Archive links are not allowed: {member.name}")
            if not member.isfile() and not member.isdir():
                raise UnsafeArchiveError("Archive may contain only regular files and directories.")
            if member.size > self.limits.max_member_bytes:
                raise UnsafeArchiveError(f"Archive member size exceeds the limit: {member.name}")
            total_size += member.size
            if total_size > self.limits.max_total_bytes:
                raise UnsafeArchiveError("Archive expanded size exceeds the limit.")
            self.check_path_conflict(paths, parts, member.isdir())
            paths[parts] = member.isdir()
            members.append(member)
        return members

    @staticmethod
    def get_safe_parts(name: str) -> tuple[str, ...]:
        """The member path split into parts; raise for an absolute or escaping path."""
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts or "\0" in name:
            raise UnsafeArchiveError(f"Archive contains an unsafe path: {name}")
        parts = tuple(part for part in path.parts if part not in ("", "."))
        if not parts and name not in (".", "./"):
            raise UnsafeArchiveError(f"Archive contains an unsafe path: {name}")
        return parts

    @staticmethod
    def check_path_conflict(
        paths: dict[tuple[str, ...], bool], parts: tuple[str, ...], is_directory: bool
    ) -> None:
        """Raise when a member repeats a path, or a file and a directory share one."""
        if parts in paths:
            raise UnsafeArchiveError(f"Archive contains a duplicate path: {'/'.join(parts)}")
        if any(paths.get(parts[:index]) is False for index in range(1, len(parts))):
            raise UnsafeArchiveError(f"Archive path crosses a regular file: {'/'.join(parts)}")
        if not is_directory and any(path[: len(parts)] == parts for path in paths):
            raise UnsafeArchiveError(f"Archive path conflicts with a directory: {'/'.join(parts)}")

    @classmethod
    def get_target(cls, root: Path, name: str) -> Path:
        """Where a member lands under root; raise when it would land outside."""
        target = root.joinpath(*cls.get_safe_parts(name))
        cls.reject_symlink_in_path(root, target)
        target = target.resolve(strict=False)
        if not target.is_relative_to(root):
            raise UnsafeArchiveError(f"Archive contains an unsafe path: {name}")
        return target

    @staticmethod
    def reject_symlink_in_path(root: Path, target: Path) -> None:
        """Raise when any directory between root and target is a symbolic link."""
        current = root
        for part in target.relative_to(root).parts:
            current /= part
            if current.is_symlink():
                raise UnsafeArchiveError(f"Archive target crosses a symlink: {current}")

    @classmethod
    def write_member(
        cls, archive: tarfile.TarFile, member: tarfile.TarInfo, root: Path, target: Path
    ) -> None:
        """Write one file member to a new file, never through a link; remove it on failure."""
        target.parent.mkdir(parents=True, exist_ok=True)
        cls.reject_symlink_in_path(root, target)
        if target.exists():
            target.unlink()
        source = archive.extractfile(member)
        if source is None:
            raise UnsafeArchiveError(f"Could not read archive member: {member.name}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
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
