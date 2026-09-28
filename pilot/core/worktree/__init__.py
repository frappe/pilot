from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config import AdminConfig, BenchConfig, ProductionConfig, WorkerConfig, WorkerGroup, WorktreeConfig
from pilot.core.bench.ports import FRONTEND_BASE_PORT
from pilot.core.worktree.site_database import get_site_database
from pilot.exceptions import BenchError
from pilot.internal.git import GitRepo
from pilot.utils import get_yarn_bin

if TYPE_CHECKING:
    from pilot.core.bench import Bench

_WORKTREES_DIRECTORY = "worktrees"
_ASSET_COMMANDS = frozenset({"build", "watch"})


class Worktree:
    """An app on its own branch, served from an overlay bench beside the main one.

    The overlay at `<bench>/worktrees/<name>` holds a git worktree of the app, links to
    main's other apps and env, and a clone of the base site. Its processes use offset ports."""

    def __init__(self, bench: "Bench", config: WorktreeConfig) -> None:
        self.bench = bench
        self.config = config

    @property
    def path(self) -> Path:
        return self.bench.path / _WORKTREES_DIRECTORY / self.config.name

    @property
    def app_path(self) -> Path:
        return self.path / "apps" / self.config.app

    @property
    def main_app_path(self) -> Path:
        return self.bench.apps_path / self.config.app

    @property
    def site_name(self) -> str:
        return f"{self.config.name}.{self.config.base_site}"

    @property
    def branch(self) -> str:
        return GitRepo(self.app_path).branch

    @property
    def env(self) -> dict[str, str]:
        """Put the worktree app ahead of main's copy, and point Frappe at the overlay."""
        return {"PYTHONPATH": str(self.app_path), "FRAPPE_BENCH_ROOT": str(self.path)}

    @property
    def frappe_source_path(self) -> Path:
        """Frappe's source as the overlay sees it: the worktree itself, or a link to main's."""
        return self.path / "apps" / "frappe"

    @property
    def frontend_port(self) -> int:
        """The port the frappe-ui plugin starts the app's Vite dev server on."""
        return FRONTEND_BASE_PORT + self.config.port_offset

    @property
    def frontend_path(self) -> Path | None:
        """The app's frontend directory, when its package.json has a `dev` script."""
        path = self.app_path / "frontend"
        package_json = path / "package.json"
        if package_json.exists() and "dev" in json.loads(package_json.read_text()).get("scripts", {}):
            return path
        return None

    @property
    def has_asset_bundles(self) -> bool:
        """Whether the app has `*.bundle.*` sources for Frappe's esbuild to watch."""
        public = self.app_path / self.config.app / "public"
        for _directory, subdirectories, files in os.walk(public):
            subdirectories[:] = [name for name in subdirectories if name not in ("node_modules", "dist")]
            if any(".bundle." in name for name in files):
                return True
        return False

    @property
    def is_running(self) -> bool:
        from pilot.core.worktree.processes import WorktreeProcessManager

        return WorktreeProcessManager(self).is_running()

    @cached_property
    def runtime_bench(self) -> "Bench":
        """The overlay as a Bench: main's config on this worktree's ports, one worker, no admin.
        Admin port 0, so nothing keyed on the admin port can reach main's admin."""
        from pilot.core.bench import Bench

        config = copy.deepcopy(self.bench.config)
        bases = BenchConfig.default_ports()
        offset = self.config.port_offset
        config.http_port = bases["http_port"] + offset
        config.socketio_port = bases["socketio_port"] + offset
        config.redis.cache_port = bases["redis.cache_port"] + offset
        config.redis.queue_port = bases["redis.queue_port"] + offset
        config.workers = WorkerConfig(groups=[WorkerGroup(queues=config.workers.queues, count=1)])
        config.watch_admin_js = False
        config.production = ProductionConfig()
        config.admin = AdminConfig(port=0)
        config.worktrees = []
        return Bench(config, self.path)

    def build_assets(self) -> None:
        """Install the app's JS dependencies and build only its assets, inside the overlay.

        Runs Frappe's esbuild directly: `frappe build` relinks every app's assets and runs
        the page-island hook, and both write into main's checkouts through `sites/assets`.
        `CI` makes esbuild exit non-zero on a failed build; without it, esbuild logs and exits 0."""
        from pilot.core.worktree.layout import WorktreeLayout
        from pilot.managers.environment import PythonEnvManager
        from pilot.managers.python_assets import PythonAssetBuilder

        runtime = self.runtime_bench
        manager = PythonEnvManager(runtime)
        builder = PythonAssetBuilder(manager)
        if (self.app_path / "package.json").exists():
            builder.ensure_yarn_install(self.app_path)
        WorktreeLayout(self).link_node_modules()
        builder.ensure_frontend_dependencies(runtime.app(self.config.app))
        builder.run_compiler(
            [get_yarn_bin(), "run", "build", "--apps", self.config.app, "--run-build-command"],
            cwd=self.frappe_source_path,
            env={**manager.get_build_env(), **self.env, "CI": "1"},
            stream_output=True,
        )

    def start(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        """Run the worktree's processes in the foreground, like `pilot start`."""
        from pilot.core.worktree.layout import WorktreeLayout
        from pilot.core.worktree.processes import WorktreeProcessManager

        manager = WorktreeProcessManager(self)
        if manager.is_running():
            raise BenchError(f"Worktree '{self.config.name}' is already running.")
        WorktreeLayout(self).sync()
        manager.write_config()
        on_progress(f"Serving {self.site_name} on port {self.runtime_bench.config.http_port}.")
        manager.start()

    def stop(self) -> None:
        from pilot.core.worktree.processes import WorktreeProcessManager

        WorktreeProcessManager(self).stop()

    def remove(
        self,
        delete_branch: bool = False,
        force: bool = False,
        on_progress: Callable[[str], None] = lambda message: None,
    ) -> None:
        """Stop, drop the clone's database, remove the checkout and the branch, then the overlay
        and the record. `delete_branch` deletes only a merged branch.

        A dirty checkout is refused before anything changes, with the changed files listed.
        Each step skips what is already gone, so a remove that failed part way can run again:
        - The drop fails: the checkout, overlay and record stay.
        - Git refuses a checkout that got dirty after the check: the edits stay, but the
          database is already dropped. Commit them, or pass `force` to discard them.
        - A later step fails: the branch is already handled, and a retry finishes the rest."""
        has_checkout = self.app_path.exists()
        if has_checkout and not force:
            self._refuse_changes()
        db_name = self.get_database_to_drop()
        branch = self.branch if has_checkout else ""
        if self.is_running:
            on_progress(f"Stopping worktree '{self.config.name}'...")
            self.stop()
        self.drop_database(db_name, on_progress)
        repo = GitRepo(self.main_app_path)
        if has_checkout:
            self.remove_checkout(repo, force)
        if delete_branch:
            _delete_merged_branch(repo, branch, on_progress)
        if self.path.exists():
            # rmtree unlinks symlinks without following them, so main's apps and env are safe.
            shutil.rmtree(self.path)
        with BenchConfig.open(self.bench.path) as config:
            config.worktrees = [record for record in config.worktrees if record.name != self.config.name]
        repo.prune_worktrees()

    def remove_checkout(self, repo: GitRepo, force: bool) -> None:
        """`git worktree remove`, which refuses a dirty checkout unless `force` is given."""
        try:
            repo.remove_worktree(self.app_path, force=force)
        except BenchError:
            if not force:
                self._refuse_changes()
            raise

    def _refuse_changes(self) -> None:
        if changed_files := GitRepo(self.app_path).changed_files:
            raise BenchError(
                f"Worktree '{self.config.name}' has uncommitted changes:\n"
                + "\n".join(f"  {line}" for line in changed_files)
                + "\nCommit them, or pass --force to discard them."
            )

    def get_database_to_drop(self) -> str:
        """The server database the clone owns, or "" when there is none to drop.
        Refuses when the clone's site config and the record name different databases."""
        if self.bench.config.db_type == "sqlite":
            return ""
        path = self.runtime_bench.sites_path / self.site_name / "site_config.json"
        if not path.exists():
            # An add that failed before the clone wrote its config made no database.
            return ""
        site_db_name = json.loads(path.read_text()).get("db_name", "")
        if site_db_name != self.config.db_name:
            raise BenchError(
                f"Worktree '{self.config.name}' records database '{self.config.db_name}', but {path} "
                f"names '{site_db_name}'. Pilot drops only the database it recorded, so it removed "
                f"nothing. Set db_name in that file back to '{self.config.db_name}', then remove again."
            )
        return site_db_name

    def drop_database(self, db_name: str, on_progress: Callable[[str], None]) -> None:
        """Drop the database from `get_database_to_drop` and its account. "" drops nothing."""
        if db_name:
            on_progress(f"Dropping database '{db_name}' and its account...")
            get_site_database(self.bench.config).drop(db_name)

    def frappe(self, args: Sequence[str]) -> int:
        """Run a Frappe CLI command with the worktree's code and sites. Returns its exit code."""
        _refuse_asset_command(args)
        runtime = self.runtime_bench
        result = subprocess.run(
            [*runtime.frappe_call, "frappe", *args],
            cwd=runtime.sites_path,
            env={**os.environ, **self.env},
        )
        return result.returncode


def _delete_merged_branch(repo: GitRepo, branch: str, on_progress: Callable[[str], None]) -> None:
    if not branch:
        on_progress("Skipped the branch: the checkout was already gone, so its name is unknown.")
        return
    try:
        repo.delete_branch(branch)
    except BenchError as error:
        on_progress(
            f"Kept branch '{branch}': {error}\nDelete it anyway with: git -C {repo.path} branch -D {branch}"
        )
    else:
        on_progress(f"Deleted branch '{branch}'.")


def _refuse_asset_command(args: Sequence[str]) -> None:
    """`frappe build` relinks every app's assets and `frappe watch` runs the page-island
    watcher. Both write into main's checkouts through `sites/assets`, even when scoped to
    one app, so Pilot builds and watches instead."""
    command = _frappe_command_name(args)
    if command in _ASSET_COMMANDS:
        raise BenchError(
            f"'frappe {command}' can write into the main bench's apps, so worktrees do not run it. "
            f"'pilot worktree add' builds the app's assets; 'pilot worktree start' runs its watcher and Vite."
        )


def _frappe_command_name(args: Sequence[str]) -> str:
    """The Frappe subcommand, skipping global options such as `--site NAME`."""
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
        elif arg == "--site":
            skip_next = True
        elif not arg.startswith("-"):
            return arg
    return ""
