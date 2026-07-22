from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from pilot.internal.git import GitRepo
from pilot.utils import installed_app_version


@dataclass
class AppInfo:
    name: str
    title: str
    description: str
    repo: str
    branch: str
    is_cloned: bool
    current_commit: str
    commit_message: str
    has_local_changes: bool
    installed_version: str
    has_update: bool


class AppProvider:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def get_all(self) -> list[AppInfo]:
        apps_path = self._bench_root / "apps"
        if not apps_path.is_dir():
            return []

        return [
            self.get_app(d.name) for d in sorted(apps_path.iterdir()) if d.is_dir() and (d / ".git").exists()
        ]

    def get_app(self, name: str) -> AppInfo:
        app_path = self._bench_root / "apps" / name
        repo = GitRepo(app_path)
        title, description = self.get_pyproject_meta(app_path, name)

        app_info = AppInfo(
            name=name,
            title=title,
            description=description,
            repo="",
            branch="",
            is_cloned=False,
            current_commit="",
            commit_message="",
            has_local_changes=False,
            installed_version=installed_app_version(self._bench_root / "env", name),
            has_update=False,
        )
        if not repo.is_cloned:
            return app_info

        sha = repo.head_sha
        remote_sha = repo.tracking_sha(repo.branch)
        app_info.is_cloned = True
        app_info.repo = repo.remote_url
        app_info.branch = repo.branch
        app_info.current_commit = sha[:7]
        app_info.commit_message = repo.commit_subject(sha)
        app_info.has_local_changes = repo.has_local_changes
        app_info.has_update = bool(remote_sha and sha and remote_sha != sha)
        return app_info

    def get_pyproject_meta(self, app_path: Path, name: str) -> tuple[str, str]:
        """Title and description from pyproject.toml, defaulting to the folder name."""
        pyproject = app_path / "pyproject.toml"
        if not pyproject.exists():
            return name, ""

        try:
            project = tomllib.loads(pyproject.read_text()).get("project") or {}
        except (tomllib.TOMLDecodeError, OSError):
            return name, ""
        title = (project.get("name") or "").strip() or name
        description = (project.get("description") or "").strip()
        return title, description
