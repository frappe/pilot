from __future__ import annotations

import copy
import shutil
from pathlib import Path

from pilot.config import BenchConfig
from pilot.core.bench.artifacts import BenchArtifacts
from pilot.core.bench.clone_branches import clone_branch, validate_branches
from pilot.exceptions import BenchError
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
    destination = source.clone(name, on_progress, branch=branch, app_branches=app_branches)
    fixture.clone(f"{name}.localhost", destination, on_progress=on_progress)
    return destination


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

    def run(self, on_progress=print):
        from pilot.core.bench import Bench
        from pilot.managers.environment import PythonEnvManager

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
        on_progress("Creating Python environment with destination editable paths")
        environment = PythonEnvManager(destination)
        environment.create_venv()
        for app in destination.apps():
            environment.install_app(app)
        if rebuild:
            on_progress("Installing Node dependencies and rebuilding assets for selected branches")
            environment.install_node_dependencies()
            environment.build_assets()
        destination.enforce_lite_mode_rules()
        on_progress(f"Bench '{self.name}' cloned; clone a site or create a new one next.")
        return destination

    def copy_apps(self, destination) -> None:
        for app in self.source.apps():
            selected = self.app_branches.get(app.config.name, self.branch)
            target_app = destination.apps_path / app.config.name
            if selected == "current":
                self.copy_app(app.path, target_app)
            else:
                selected = clone_branch(app.path, target_app, selected)
                next(
                    item for item in destination.config.apps if item.name == app.config.name
                ).branch = selected
        destination.config.write(destination.path)

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
            return
        git_file.unlink()
        staging = destination / ".pilot-clone-git"
        run_command(["git", "clone", "--no-hardlinks", "--no-checkout", str(source), str(staging)])
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
