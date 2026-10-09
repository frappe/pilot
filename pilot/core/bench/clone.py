from __future__ import annotations

import copy
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pilot.config import BenchConfig
from pilot.core.bench.artifacts import BenchArtifacts
from pilot.core.bench.clone_branches import clone_branch, validate_branches
from pilot.exceptions import BenchError
from pilot.integrations.git import auth_config_for
from pilot.utils import run_command


def clone_with_site(source, name: str, site: str, on_progress, branch="default", app_branches=None):
    BenchConfig.default(name).validate()
    if (source.path.parent / name).exists():
        raise BenchError(f"Destination bench '{name}' already exists.")
    if site:
        fixture = source.site(site)
    else:
        sites = source.sites()
        if len(sites) != 1:
            raise BenchError("Select the source site with --site when the bench has multiple sites.")
        fixture = sites[0]
    from pilot.internal.validators import validate_site_name

    if error := validate_site_name(fixture.config.name):
        raise BenchError(error)
    if not fixture.exists:
        raise BenchError("Source site does not exist.")
    return BenchClone(source, name, branch, app_branches).run(on_progress, site_source=fixture)


class BenchClone:
    """Copy code and dependencies into an independent, site-free development bench."""

    def __init__(self, source, name: str, branch="default", app_branches=None) -> None:
        self.source = source
        self.name = name
        self.branch = branch
        self.app_branches = app_branches if app_branches is not None else {}
        validate_branches(branch, self.app_branches)
        unknown = set(self.app_branches) - {app.config.name for app in source.apps()}
        if unknown:
            raise BenchError(f"Unknown app branch overrides: {', '.join(sorted(unknown))}")

    def run(self, on_progress=print, *, site_source=None):
        from pilot.core.bench import Bench

        BenchConfig.default(self.name).validate()
        target = self.source.path.parent / self.name
        if target.exists():
            raise BenchError(f"Destination bench '{self.name}' already exists.")
        missing = set(self.source.registered_apps()) - {app.module_name for app in self.source.apps()}
        if missing:
            raise BenchError(f"Source apps must be Git repositories: {', '.join(sorted(missing))}")
        destination = Bench.create_at(target, self.name, db_type=self.source.config.db_type)
        self.configure(destination)
        destination.create_directories()
        destination.write_common_site_config()
        on_progress("Copying apps and independent Git repositories")
        self.copy_apps(destination)
        rebuild = any(
            self.app_branches.get(app.config.name, self.branch) != "current" for app in self.source.apps()
        )
        assets = self.source.sites_path / "assets"
        if not rebuild and assets.is_dir():
            on_progress("Copying assets")
            BenchArtifacts.copy_directory(assets, destination.sites_path / "assets")
        BenchArtifacts.relocate_links(self.source.path.resolve(), destination.path.resolve())
        destination.write_apps_txt()
        if site_source is None:
            self.prepare_environment(destination, rebuild, on_progress)
        else:
            from pilot.core.site.clone import SiteClone

            SiteClone(site_source, destination, f"{self.name}.localhost", "admin").run(
                on_progress,
                prepare_bench=lambda: self.prepare_environment(destination, rebuild, on_progress),
            )
        destination.enforce_lite_mode_rules()
        on_progress(f"Bench '{self.name}' cloned.")
        return destination

    def prepare_environment(self, destination, rebuild: bool, on_progress) -> None:
        from pilot.core.bench.build_artifacts import BuildArtifacts
        from pilot.managers.environment import PythonEnvManager

        on_progress("Creating Python environment with destination editable paths")
        environment = PythonEnvManager(destination)
        with ThreadPoolExecutor(max_workers=1) as executor:
            dependencies = executor.submit(self.copy_dependencies, destination) if rebuild else None
            environment.create_venv()
            environment.install_apps(destination.apps())
            if dependencies is not None:
                dependencies.result()
        if rebuild:
            on_progress("Checking Node dependencies for selected branches")
            environment.install_node_dependencies()
            from pilot.managers.node_cache import NodeDependencyCache

            cache = NodeDependencyCache(destination)
            for app in destination.apps():
                if self.app_branches.get(app.config.name, self.branch) == "current":
                    continue
                for relative in (".", "frontend", "roster"):
                    cache.capture(app.path / relative)
            artifacts = BuildArtifacts(destination)
            if artifacts.restore(artifacts.get_key()):
                on_progress("Reusing matching built assets")
            else:
                on_progress("Installing Node dependencies and rebuilding assets for selected branches")
                environment.build_assets()

    def copy_dependencies(self, destination) -> None:
        from pilot.managers.node_cache import NodeDependencyCache
        from pilot.managers.node_dependencies import NodeDependencies

        cache = NodeDependencyCache(destination)
        for app in self.source.apps():
            for relative in (".", "frontend", "roster"):
                target = destination.apps_path / app.config.name / relative
                if self.app_branches.get(app.config.name, self.branch) != "current" and cache.restore(target):
                    continue
                if NodeDependencies.copy(app.path / relative, target):
                    BenchArtifacts.relocate_links(
                        self.source.path.resolve(), destination.path.resolve(), root=target / "node_modules"
                    )

    def copy_apps(self, destination) -> None:
        apps = self.source.apps()
        with ThreadPoolExecutor(max_workers=4) as executor:
            branches = list(executor.map(self.copy_selected_app, apps, [destination] * len(apps)))
        for app, selected in zip(apps, branches, strict=True):
            if selected is not None:
                next(
                    item for item in destination.config.apps if item.name == app.config.name
                ).branch = selected
        destination.config.write(destination.path)

    def copy_selected_app(self, app, destination) -> str | None:
        selected = self.app_branches.get(app.config.name, self.branch)
        target = destination.apps_path / app.config.name
        if selected == "current":
            self.copy_app(app.path, target)
            return None
        return clone_branch(app.path, target, selected, auth_config_for(app.bench.path, app.config.repo))

    def configure(self, destination) -> None:
        for name in (
            "python_version",
            "mariadb",
            "postgres",
            "workers",
            "socketio_backend",
            "install_dev_extra",
            "allow_developer_mode",
        ):
            setattr(destination.config, name, copy.deepcopy(getattr(self.source.config, name)))
        destination.config.apps = [copy.deepcopy(app.config) for app in self.source.apps()]
        configured = {app.name: app.repo for app in self.source.config.apps}
        for app in destination.config.apps:
            app.repo = app.repo or configured.get(app.name) or str(self.source.apps_path / app.name)
        destination.config.watch_admin_js = False
        destination.config.write(destination.path)

    @staticmethod
    def copy_app(source: Path, destination: Path) -> None:
        BenchArtifacts.copy_directory(source, destination)
        # A linked worktree's .git file points back to its owner. Materialize its
        # repository, retaining copied dirty/untracked files in the destination.
        git_file = destination / ".git"
        if not git_file.is_file() and not git_file.is_symlink():
            if (worktrees := git_file / "worktrees").exists():
                shutil.rmtree(worktrees)
            return
        git_file.unlink()
        staging = destination / ".pilot-clone-git"
        from pilot.core.bench.clone_repository import clone_repository

        clone_repository(source, staging)
        (staging / ".git").rename(git_file)
        staging.rmdir()
        index = (
            run_command(
                ["git", "-C", str(source), "rev-parse", "--path-format=absolute", "--git-path", "index"]
            )
            .stdout.decode()
            .strip()
        )
        if Path(index).is_file():
            shutil.copy2(index, git_file / "index")
        else:
            run_command(["git", "-C", str(destination), "read-tree", "HEAD"])
        from pilot.internal.git import GitRepo

        if remote := GitRepo(source).remote_url:
            run_command(["git", "-C", str(destination), "remote", "set-url", "origin", remote])
        else:
            run_command(["git", "-C", str(destination), "remote", "remove", "origin"])
