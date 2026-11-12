from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path, PurePosixPath

from admin.backend.core.editor.git import EditorGit


class EditorPathError(ValueError):
    """Requested path escapes the workspace root."""


class HiddenPathError(ValueError):
    """Path is a dot file or inside a dot folder, such as `.git` or `.env`."""


class BinaryFileError(ValueError):
    """File is not valid UTF-8 text."""


class EditorWorkspace:
    """One installed app's directory, with path-safe file operations."""

    file_list_cap = 20000

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root_real = self.root.resolve()
        self.git = EditorGit(self.root)

    def safe(self, rel: str) -> Path:
        """Resolve a client path against the root, rejecting traversal, symlink escapes and dot files."""
        clean = _clean_relative(rel)
        target = self.root / clean if clean else self.root
        try:
            resolved = target.resolve(strict=True)
        except FileNotFoundError:
            resolved = target.resolve()
        # A symlink loop, or a file used as a folder.
        except (OSError, RuntimeError) as error:
            raise EditorPathError(rel) from error
        if resolved != self.root_real and self.root_real not in resolved.parents:
            raise EditorPathError(rel)
        if _is_hidden(clean) or _is_hidden(resolved.relative_to(self.root_real).as_posix()):
            raise HiddenPathError(rel)
        return target

    def relative(self, rel: str) -> str:
        """Validated in-app path, as git and the API expect to see it."""
        return self.rel(self.safe(rel))

    def changeable(self, rel: str) -> str:
        """Validated in-app path whose whole tree a git command may change: no dot file inside."""
        target = self.safe(rel)
        _refuse_hidden_inside(target)
        return self.rel(target)

    def rel(self, abs_path: Path) -> str:
        """The path relative to the app."""
        if abs_path == self.root:
            return "."
        return abs_path.relative_to(self.root).as_posix()

    def get_commit_blockers(self, stage_all: bool) -> list[str]:
        """Repository paths a commit would take that the editor may not change: dot files or files
        outside the app. Commit all stages only the app folder, so outside changes block only when staged."""
        prefix = self.git.prefix
        blockers = []
        for code, path in self.git.get_changes():
            is_staged = code[0] not in " ?"
            is_inside = path.startswith(prefix)
            if not is_staged and not (stage_all and is_inside):
                continue
            if not is_inside or _is_hidden(path[len(prefix) :]):
                blockers.append(path)
        return blockers

    def tree(self, rel: str) -> list[dict]:
        """The entries of a folder."""
        base = self.safe(rel)
        entries = []
        for item in os.scandir(base):
            kind = "dir" if item.is_dir(follow_symlinks=False) else "file"
            info = item.stat(follow_symlinks=False)
            entries.append(
                {
                    "name": item.name,
                    "path": self.rel(Path(item.path)),
                    "type": kind,
                    "size": info.st_size,
                    "mtime": int(info.st_mtime),
                }
            )
        self._mark_ignored(entries)
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"]))
        return entries

    def _mark_ignored(self, entries: list[dict]) -> None:
        """Mark the entries git ignores."""
        if not self.git.is_repo:
            return
        ignored = self.git.ignored_set([e["path"] for e in entries])
        for entry in entries:
            if entry["name"] == ".git" or entry["path"] in ignored:
                entry["ignored"] = True

    def read(self, rel: str) -> dict:
        """The text of a file with its ETag; raise for a binary file."""
        raw = self.safe(rel).read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise BinaryFileError(rel) from error
        return {"path": rel, "content": content, "etag": _etag(raw)}

    def save(self, rel: str, content: str, if_match: str) -> tuple[int, dict]:
        """Save a file if its ETag matches; return the status and result."""
        target = self.safe(rel)
        body = content.encode("utf-8")
        if target.exists():
            current = target.read_bytes()
            current_tag = _etag(current)
            if if_match != "*" and if_match != current_tag:
                return 409, {
                    "path": rel,
                    "content": current.decode("utf-8", "replace"),
                    "etag": current_tag,
                }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return 200, {"etag": _etag(body)}

    def create(self, rel: str, kind: str) -> dict:
        """Create a file or folder."""
        target = self.safe(rel)
        if target.exists():
            raise FileExistsError(rel)
        if kind == "dir":
            target.mkdir(parents=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"")
        return {"path": self.rel(target)}

    def rename(self, frm: str, to: str) -> dict:
        """Rename a file or folder."""
        source = self.safe(frm)
        destination = self.safe(to)
        _refuse_hidden_inside(source)
        if source.resolve() in [destination.resolve(), *destination.resolve().parents]:
            raise EditorPathError(to)
        if destination.exists():
            raise FileExistsError(to)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
        return {"path": self.rel(destination)}

    def delete(self, rel: str) -> None:
        """Delete a file or folder; refuse the app root."""
        target = self.safe(rel)
        if target == self.root or target.resolve() == self.root_real:
            raise EditorPathError("cannot delete root")
        _refuse_hidden_inside(target)
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()

    def list_files(self) -> list[str]:
        """Every file in the app."""
        if self.git.is_repo:
            tracked = self.git.list_files(self.file_list_cap)
            if tracked is not None:
                return [path for path in tracked if not _is_hidden(path)]
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d != "node_modules" and not d.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                out.append(self.rel(Path(dirpath) / name))
                if len(out) >= self.file_list_cap:
                    return out
        return out


def _clean_relative(rel: str) -> str:
    """Normalize a client path to an in-app relative path.

    Absolute paths and ".." are rejected rather than collapsed: a request that
    tries to leave the app should fail, not silently land on a different file.
    """
    if not isinstance(rel, str):
        raise EditorPathError(rel)
    text = rel.strip()
    if "\x00" in text or text.startswith("/"):
        raise EditorPathError(rel)
    segments = []
    for segment in text.split("/"):
        if segment == "..":
            raise EditorPathError(rel)
        if segment not in ("", "."):
            segments.append(segment)
    return "/".join(segments)


def _is_hidden(rel: str) -> bool:
    """Whether a path part is hidden."""
    return any(part.startswith(".") for part in PurePosixPath(rel).parts)


def _refuse_hidden_inside(path: Path) -> None:
    """Deleting or moving a folder must not take a `.git` or `.env` inside it along."""
    if not path.is_dir() or path.is_symlink():
        return
    for _, dirnames, filenames in os.walk(path):
        if any(name.startswith(".") for name in [*dirnames, *filenames]):
            raise HiddenPathError(str(path))


def _etag(data: bytes) -> str:
    """The ETag of the data."""
    return hashlib.sha256(data).hexdigest()[:16]
