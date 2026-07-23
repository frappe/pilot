from __future__ import annotations

import hashlib
import os
import posixpath
import shutil
from pathlib import Path

from admin.backend.core.editor.git import EditorGit

_FILE_LIST_CAP = 20000


class EditorPathError(ValueError):
    """Requested path escapes the workspace root."""


class BinaryFileError(ValueError):
    """File is not valid UTF-8 text."""


class EditorWorkspace:
    """One installed app's directory, with path-safe file operations."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root_real = self.root.resolve()
        self.git = EditorGit(self.root)

    def safe(self, rel: str) -> Path:
        """Resolve a client path against the root, rejecting traversal and symlink escapes."""
        clean = posixpath.normpath("/" + (rel or "").strip()).lstrip("/")
        target = self.root / clean if clean else self.root
        resolved = target.resolve()
        if resolved != self.root_real and self.root_real not in resolved.parents:
            raise EditorPathError(rel)
        return target

    def rel(self, abs_path: Path) -> str:
        if abs_path == self.root:
            return "."
        return abs_path.relative_to(self.root).as_posix()

    def tree(self, rel: str) -> list[dict]:
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
        if not self.git.is_repo:
            return
        ignored = self.git.ignored_set([e["path"] for e in entries])
        for entry in entries:
            if entry["name"] == ".git" or entry["path"] in ignored:
                entry["ignored"] = True

    def read(self, rel: str) -> dict:
        raw = self.safe(rel).read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise BinaryFileError(rel) from error
        return {"path": rel, "content": content, "etag": _etag(raw)}

    def save(self, rel: str, content: str, if_match: str) -> tuple[int, dict]:
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
        source = self.safe(frm)
        destination = self.safe(to)
        if destination.exists():
            raise FileExistsError(to)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
        return {"path": self.rel(destination)}

    def delete(self, rel: str) -> None:
        target = self.safe(rel)
        if target == self.root or target.resolve() == self.root_real:
            raise EditorPathError("cannot delete root")
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()

    def list_files(self) -> list[str]:
        if self.git.is_repo:
            tracked = self.git.list_files(_FILE_LIST_CAP)
            if tracked is not None:
                return tracked
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
            for name in filenames:
                out.append(self.rel(Path(dirpath) / name))
                if len(out) >= _FILE_LIST_CAP:
                    return out
        return out


def _etag(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]
