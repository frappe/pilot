from __future__ import annotations

import subprocess
import zlib
from dataclasses import dataclass
from pathlib import Path

from bench_cli.utils import git_has_local_changes


@dataclass
class AppInfo:
    name: str
    repo: str
    branch: str
    branches: list
    is_cloned: bool
    current_commit: str
    commit_message: str
    has_local_changes: bool
    installed_version: str
    has_update: bool


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

    def check_remote_updates(self, app_names: list[str]) -> dict[str, bool]:
        """Run git ls-remote concurrently for each app. Returns {app_name: has_update}.

        ls-remote only exchanges ref pointers with the remote — no object download —
        so it completes in ~1-2s per app regardless of repo size.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _check(name: str) -> tuple[str, bool]:
            app_path = self._bench_root / "apps" / name
            git_dir = app_path / ".git"
            if not git_dir.exists():
                return name, False
            branch = self._git_branch(git_dir)
            if not branch:
                return name, False
            result = subprocess.run(
                ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
                cwd=str(app_path), capture_output=True, text=True, timeout=15,
            )
            for line in result.stdout.splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    remote_sha = parts[0].strip()
                    local_sha = self._git_full_sha(git_dir)
                    return name, bool(remote_sha and local_sha and remote_sha != local_sha)
            return name, False

        updates: dict[str, bool] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_check, name): name for name in app_names}
            for fut in as_completed(futures):
                name, has_update = fut.result()
                updates[name] = has_update
        return updates

    def list_commits(self, app_name: str, depth: int = 10) -> list[dict]:
        """Shallow-fetch the remote branch tip and return new commits not in HEAD.

        Only fetches `depth` commits from the remote, so it's fast even on large repos.
        Called on demand when the user opens the Update modal for a specific app.
        """
        app_path = self._bench_root / "apps" / app_name
        if not (app_path / ".git").exists():
            return []
        info = self.read_one(app_name)
        if not info.branch:
            return []
        subprocess.run(
            ["git", "fetch", "origin", info.branch, f"--depth={depth}", "--quiet"],
            cwd=str(app_path), capture_output=True, timeout=30,
        )
        result = subprocess.run(
            ["git", "log", "HEAD..FETCH_HEAD", "--format=%h\x1f%s\x1f%an\x1f%ar", f"--max-count={depth}"],
            cwd=str(app_path), capture_output=True, text=True,
        )
        commits = []
        for line in result.stdout.splitlines():
            parts = line.split("\x1f", 3)
            if len(parts) == 4:
                commits.append({"hash": parts[0], "message": parts[1], "author": parts[2], "date": parts[3]})
        return commits

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
                has_local_changes=False,
                installed_version=self._pip_version(name),
                has_update=False,
            )

        git_dir = app_path / ".git"
        sha = self._git_full_sha(git_dir)
        branch = self._git_branch(git_dir)
        remote_sha = self._git_remote_tracking_sha(git_dir, branch)
        return AppInfo(
            name=name,
            repo=self._git_remote(git_dir),
            branch=branch,
            branches=[],
            is_cloned=True,
            current_commit=sha[:7] if sha else "",
            commit_message=self._git_commit_message(git_dir, sha),
            has_local_changes=git_has_local_changes(app_path),
            installed_version=self._pip_version(name),
            has_update=bool(remote_sha and sha and remote_sha != sha),
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
            return content[len("ref: refs/heads/") :]
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
            content = raw[null_idx + 1 :].decode("utf-8", errors="replace")
            headers_end = content.find("\n\n")
            if headers_end >= 0:
                return content[headers_end + 2 :].split("\n")[0].strip()
        except Exception:
            pass
        return ""

    def _git_remote_tracking_sha(self, git_dir: Path, branch: str) -> str:
        if not branch:
            return ""
        ref_file = git_dir / "refs" / "remotes" / "origin" / branch
        if ref_file.exists():
            return ref_file.read_text().strip()
        return self._read_packed_ref(git_dir, f"refs/remotes/origin/{branch}")

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
