from __future__ import annotations

import subprocess
import zlib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppInfo:
    name: str
    repo: str
    branch: str
    branches: list
    is_cloned: bool
    current_commit: str
    commit_message: str
    uncommitted_changes: bool
    installed_version: str


class AppReader:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def read_all(self) -> list[AppInfo]:
        apps_path = self._bench_root / "apps"
        if not apps_path.is_dir():
            return []
        return [
            self._read_app(d.name)
            for d in sorted(apps_path.iterdir())
            if d.is_dir() and (d / ".git").exists()
        ]

    def read_one(self, app_name: str) -> AppInfo:
        return self._read_app(app_name)

    def _read_app(self, name: str) -> AppInfo:
        app_path = self._bench_root / "apps" / name
        is_cloned = (app_path / ".git").exists()

        if not is_cloned:
            return AppInfo(
                name=name,
                repo="",
                branch="",
                branches=[],
                is_cloned=False,
                current_commit="",
                commit_message="",
                uncommitted_changes=False,
                installed_version=self._pip_version(name),
            )

        git_dir = app_path / ".git"
        sha = self._git_full_sha(git_dir)
        return AppInfo(
            name=name,
            repo=self._git_remote(git_dir),
            branch=self._git_branch(git_dir),
            branches=[],
            is_cloned=True,
            current_commit=sha[:7] if sha else "",
            commit_message=self._git_commit_message(git_dir, sha),
            uncommitted_changes=self._git_is_dirty(app_path),
            installed_version=self._pip_version(name),
        )

    def _git_remote(self, git_dir: Path) -> str:
        config = git_dir / "config"
        if not config.exists():
            return ""
        in_origin = False
        for line in config.read_text(errors="replace").splitlines():
            line = line.strip()
            if line.startswith("["):
                in_origin = line.startswith('[remote "origin"')
            elif in_origin and line.startswith("url"):
                return line.split("=", 1)[1].strip()
        return ""

    def _git_branch(self, git_dir: Path) -> str:
        head = git_dir / "HEAD"
        if not head.exists():
            return ""
        content = head.read_text().strip()
        if content.startswith("ref: refs/heads/"):
            return content[len("ref: refs/heads/"):]
        return ""

    def _git_full_sha(self, git_dir: Path) -> str:
        head = git_dir / "HEAD"
        if not head.exists():
            return ""
        content = head.read_text().strip()
        if content.startswith("ref: "):
            ref = content[5:]
            ref_file = git_dir / ref
            if ref_file.exists():
                return ref_file.read_text().strip()
            return self._read_packed_ref(git_dir, ref)
        return content

    def _read_packed_ref(self, git_dir: Path, ref: str) -> str:
        packed = git_dir / "packed-refs"
        if not packed.exists():
            return ""
        for line in packed.read_text(errors="replace").splitlines():
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == ref:
                return parts[0]
        return ""

    def _git_commit_message(self, git_dir: Path, sha: str) -> str:
        if not sha or len(sha) < 4:
            return ""
        obj_path = git_dir / "objects" / sha[:2] / sha[2:]
        if not obj_path.exists():
            return ""
        try:
            raw = zlib.decompress(obj_path.read_bytes())
            null_idx = raw.index(b"\0")
            content = raw[null_idx + 1:].decode("utf-8", errors="replace")
            headers_end = content.find("\n\n")
            if headers_end >= 0:
                return content[headers_end + 2:].split("\n")[0].strip()
        except Exception:
            pass
        return ""

    def _git_is_dirty(self, app_path: Path) -> bool:
        result = subprocess.run(
            ["git", "-C", str(app_path), "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
        return bool(result.stdout.strip()) if result.returncode == 0 else False

    def _pip_version(self, name: str) -> str:
        lib_dir = self._bench_root / "env" / "lib"
        if not lib_dir.is_dir():
            return ""
        norm = name.replace("-", "_")
        for python_dir in lib_dir.iterdir():
            pkgs = python_dir / "site-packages"
            if not pkgs.is_dir():
                continue
            for dist_info in pkgs.glob(f"{norm}-*.dist-info"):
                metadata = dist_info / "METADATA"
                if not metadata.exists():
                    continue
                for line in metadata.read_text(errors="replace").splitlines():
                    if line.startswith("Version:"):
                        return line.split(":", 1)[1].strip()
        return ""
