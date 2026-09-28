from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import TYPE_CHECKING

from pilot.config import BenchConfig, WorktreeConfig
from pilot.core.bench.ports import pick_port_offset
from pilot.core.worktree import Worktree
from pilot.core.worktree.layout import WorktreeLayout
from pilot.core.worktree.site_clone import SiteClone
from pilot.core.worktree.site_database import get_site_database
from pilot.exceptions import BenchError
from pilot.internal.git import GitRepo

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class WorktreeCreator:
    """Adds a worktree: checks everything first, then creates it, and undoes a failed add."""

    def __init__(
        self, bench: "Bench", app: str, name: str, base_site: str, branch: str, start_point: str
    ) -> None:
        self.bench = bench
        self.app = app
        self.name = name
        self.base_site = base_site
        self.branch = branch or name
        self.start_point = start_point

    def run(self, on_progress: Callable[[str], None]) -> Worktree:
        repo = self.get_app_repo()
        worktree = Worktree(self.bench, self.get_config(repo))
        if worktree.path.exists():
            raise BenchError(f"{worktree.path} already exists.")
        clone = SiteClone(
            self.bench.site(worktree.config.base_site).path,
            worktree.runtime_bench.sites_path / worktree.site_name,
            get_site_database(self.bench.config),
            worktree.config.db_name,
        )
        creates_branch = not repo.has_branch(self.branch)
        try:
            on_progress(f"Checking out '{self.branch}' at {worktree.app_path}")
            repo.add_worktree(worktree.app_path, self.branch, self.start_point or "HEAD")
            self.populate(worktree, clone, on_progress)
        except BaseException:
            on_progress(f"Adding worktree '{self.name}' failed; removing what it created.")
            self.rollback(worktree, repo, creates_branch, on_progress)
            raise
        on_progress(f"Worktree '{self.name}' is ready. Start it with: pilot worktree start {self.name}")
        return worktree

    def rollback(
        self, worktree: Worktree, repo: GitRepo, creates_branch: bool, on_progress: Callable[[str], None]
    ) -> None:
        """Undo a failed add, including the clone's database. A branch that existed before is kept.
        Errors here are reported, not raised, so they do not hide the error that failed the add."""
        try:
            worktree.remove(force=True, on_progress=on_progress)
            if creates_branch and repo.has_branch(self.branch):
                repo.delete_branch(self.branch, force=True)
        except Exception as error:
            on_progress(f"Could not fully undo worktree '{self.name}': {error}")

    def get_config(self, repo: GitRepo) -> WorktreeConfig:
        """A validated record, checked against the bench before anything is created."""
        config = WorktreeConfig(
            self.name,
            self.app,
            self.base_site or self.get_only_site_with_app(),
            port_offset=0,
            db_name=f"_{secrets.token_hex(8)}",
        )
        config.validate()
        if any(record.name == self.name for record in self.bench.config.worktrees):
            raise BenchError(f"Worktree '{self.name}' already exists.")
        self.check_base_site(config.base_site)
        if self.start_point and (repo.has_branch(self.branch) or repo.tracking_sha(self.branch)):
            raise BenchError(f"Branch '{self.branch}' exists; --from applies only to a new branch.")
        config.port_offset = pick_port_offset(self.bench.path.parent)
        return config

    def populate(self, worktree: Worktree, clone: SiteClone, on_progress: Callable[[str], None]) -> None:
        with BenchConfig.open(self.bench.path) as bench_config:
            bench_config.worktrees.append(worktree.config)
        on_progress("Linking the main bench's apps and environment")
        WorktreeLayout(worktree).sync()
        on_progress(f"Cloning {worktree.config.base_site} to {worktree.site_name}")
        clone.run()
        on_progress(f"Building assets for {self.app}")
        worktree.build_assets()

    def get_app_repo(self) -> GitRepo:
        repo = GitRepo(self.bench.apps_path / self.app)
        if not repo.is_cloned:
            raise BenchError(f"App '{self.app}' is not a git checkout in bench '{self.bench.config.name}'.")
        return repo

    def get_only_site_with_app(self) -> str:
        sites = [site.config.name for site in self.bench.sites() if self.app in site.installed_apps()]
        if len(sites) == 1:
            return sites[0]
        if not sites:
            raise BenchError(f"No site has '{self.app}' installed. Pass --site.")
        raise BenchError(f"Several sites have '{self.app}' installed: {', '.join(sites)}. Pass --site.")

    def check_base_site(self, name: str) -> None:
        site = self.bench.site(name)
        if not site.exists:
            raise BenchError(f"Site '{name}' does not exist.")
        if self.app not in site.installed_apps():
            raise BenchError(f"App '{self.app}' is not installed on site '{name}'.")
