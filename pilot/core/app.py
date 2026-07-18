from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config.app import AppConfig
from pilot.core.app_install_result import AppInstallResult
from pilot.core.app_repository import AppRepository
from pilot.core.app_revisions import RevisionPin
from pilot.exceptions import BenchError
from pilot.utils import installed_app_version, run_command

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.internal.git import GitRepo


class App:
    def __init__(self, config: AppConfig, bench: "Bench") -> None:
        self.config = config
        self.bench = bench

    @classmethod
    def from_repo(cls, bench: "Bench", repo: str, branch: str = "") -> "App":
        """Build an App from a raw git repository URL, deriving its name from
        the repo's final path segment (e.g. '.../india-compliance.git' ->
        'india-compliance'). Rejects 'frappe' — it's the base framework, set
        up when the bench itself is created, not installable via get-app."""
        from pathlib import PurePosixPath

        name = PurePosixPath(repo.rstrip("/")).name
        if name.endswith(".git"):
            name = name[:-4]
        if name.replace("-", "_").lower() == "frappe":
            raise BenchError(
                "'frappe' is the base framework, not an app — it can't be added "
                "with get-app. It's set up when the bench itself is created."
            )
        return cls(AppConfig(name=name, repo=repo, branch=branch), bench)

    @property
    def path(self) -> Path:
        return self.bench.apps_path / self.config.name

    @property
    def installed_version(self) -> str:
        """The version pip installed for this app, read from dist-info metadata."""
        return installed_app_version(self.bench.env_path, self.config.name)

    @property
    def _repo(self) -> "GitRepo":
        return self._repository.repo

    @property
    def _repository(self) -> AppRepository:
        return AppRepository(self)

    @property
    def installed_hash(self) -> str:
        return self._repository.installed_hash

    @property
    def installed_tag(self) -> str:
        return self._repository.installed_tag

    def is_on_revision(self, pin: RevisionPin) -> bool:
        return self._repository.is_on_revision(pin)

    def has_marketplace_update(self, marketplace_entry: dict | None) -> bool:
        return self._repository.has_marketplace_update(marketplace_entry)

    def has_remote_update(self) -> bool:
        return self._repository.has_remote_update()

    @property
    def is_cloned(self) -> bool:
        # The clone may live under the configured name or, after get-app
        # normalised it, under the importable module name (e.g. india-compliance
        # -> india_compliance). Check the cheap config-name path first and only
        # resolve module_name (which reads pyproject) when that misses.
        if (self.path / ".git").exists():
            return True
        module_path = self.bench.apps_path / self.module_name
        return module_path != self.path and (module_path / ".git").exists()

    @property
    def _remote_url(self) -> str:
        return self._repository.remote_url

    def _detect_default_branch(self) -> str:
        return self._repository.detect_default_branch()

    def is_commit_hash(self, ref: str) -> bool:
        return AppRepository.is_commit_hash(ref)

    def _clone_rev(self, commit: str) -> None:
        self._repository.clone_rev(commit)

    def clone(self) -> None:
        self._repository.clone()

    @property
    def _is_shallow(self) -> bool:
        return self._repository.is_shallow

    @staticmethod
    def _pack_threads() -> int:
        return AppRepository.pack_threads()

    def update(self, pin: RevisionPin | None = None) -> None:
        self._repository.update(pin)

    def switch_branch(self, branch: str) -> None:
        self._repository.switch_branch(branch)

    def _checkout_pinned_target(self, pin: RevisionPin) -> None:
        self._repository.checkout_pinned_target(pin)

    def _checkout_pinned_commit(self, sha: str) -> None:
        self._repository.checkout_pinned_commit(sha)

    @property
    def module_name(self) -> str:
        """Return the importable Python package name for the app.

        The authoritative source is pyproject.toml's ``[project] name`` (PEP 621),
        which for Frappe apps is the importable module (e.g. 'india_compliance'
        even when the repo/folder is 'india-compliance'). Fall back to scanning
        for the subdir containing hooks.py, then to the conventional hyphen->
        underscore mapping, for older apps that ship only setup.py.
        """
        pyproject = self.path / "pyproject.toml"
        if pyproject.exists():
            import tomllib

            try:
                name = tomllib.loads(pyproject.read_text()).get("project", {}).get("name")
            except (tomllib.TOMLDecodeError, OSError):
                name = None
            if name:
                return name.replace("-", "_")

        conventional = self.config.name.replace("-", "_")
        if (self.path / conventional / "hooks.py").exists():
            return conventional
        if self.path.is_dir():
            for child in self.path.iterdir():
                if child.is_dir() and (child / "hooks.py").exists():
                    return child.name
        return conventional

    def build_assets(self) -> None:
        if not (self.path / "package.json").exists():
            return
        run_command(["yarn", "--cwd", str(self.path), "build"])

    def install(
        self,
        *,
        install_dependencies: bool = False,
        skip_validations: bool = False,
        on_progress: Callable[[str], None] = lambda message: None,
    ) -> AppInstallResult:
        """Clone (if needed), validate, install into the Python environment,
        register in apps.txt, and build assets. Installs missing marketplace
        dependencies first when requested. A clone made during this call is
        rolled back if a later step fails; an already-cloned app never is."""
        if self.bench.is_app_installed(self.config.name):
            app = self.bench.app(self.module_name)
            dependencies = app._install_dependencies(on_progress) if install_dependencies else []
            on_progress(f"'{app.config.name}' already installed, skipping.")
            return AppInstallResult(
                app, already_installed=True, installed_dependencies=dependencies
            )

        app, cloned_this_run = self._clone_and_normalize(on_progress)
        try:
            dependencies = app._install_dependencies(on_progress) if install_dependencies else []
            if not skip_validations:
                app._validate()
        except BenchError:
            if cloned_this_run:
                shutil.rmtree(app.path, ignore_errors=True)
            raise

        on_progress(f"Installing {app.config.name}...")
        app._install_into_environment()
        app._register()
        on_progress(f"\nSetting up assets for {app.config.name}...")
        app._build_assets_via_env_manager()
        on_progress(f"\n'{app.config.name}' installed successfully.")
        return AppInstallResult(app, already_installed=False, installed_dependencies=dependencies)

    def _clone_and_normalize(self, on_progress: Callable[[str], None]) -> tuple["App", bool]:
        """Clone this app if it isn't already, then move it into its
        importable-module-name folder if that differs from the requested
        name — returning the (possibly different) App for the final path."""
        cloned_this_run = False
        if self.is_cloned:
            on_progress(f"'{self.config.name}' already cloned, skipping clone.")
        else:
            on_progress(f"Cloning {self.config.name}...")
            self.clone()
            cloned_this_run = True

        module = self.module_name
        if module == self.config.name:
            return self, cloned_this_run
        target = self.bench.apps_path / module
        if not target.exists():
            self.path.rename(target)
        renamed = App(
            AppConfig(name=module, repo=self.config.repo, branch=self.config.branch), self.bench
        )
        return renamed, cloned_this_run

    def _install_dependencies(self, on_progress: Callable[[str], None]) -> list["App"]:
        from pilot.core.app_dependency_installer import AppDependencyInstaller

        return AppDependencyInstaller(self.bench, self).install(on_progress)

    def _validate(self) -> None:
        from pilot.core.app_validator import Validator

        Validator(self).validate()

    def _install_into_environment(self) -> None:
        from pilot.managers.python_environment import PythonEnvManager

        PythonEnvManager(self.bench).install_app(self)

    def _register(self) -> None:
        existing = self.bench.registered_apps()
        if self.config.name not in existing:
            (self.bench.sites_path / "apps.txt").write_text(
                "\n".join(existing + [self.config.name]) + "\n"
            )

    def _build_assets_via_env_manager(self) -> None:
        from pilot.managers.python_environment import PythonEnvManager

        PythonEnvManager(self.bench).build_assets_for_app(self)

    def ensure_removable(self) -> None:
        if not self.path.exists():
            raise BenchError(f"App '{self.config.name}' not found in bench.")
        framework = self.bench.config.framework_app.name
        if self.config.name == framework:
            raise BenchError(f"Cannot remove the framework app '{framework}'.")

    def remove(
        self, force: bool = False, on_progress: Callable[[str], None] = lambda message: None
    ) -> None:
        """Uninstall from every site it's installed on, deregister from
        apps.txt, uninstall from the Python environment, and delete its
        folder. With force=True, a site uninstall failure is reported and
        skipped rather than aborting the whole removal."""
        self.ensure_removable()
        self._uninstall_from_all_sites(force, on_progress)
        self._deregister()
        on_progress(f"Removing '{self.config.name}' from Python environment...")
        self._pip_uninstall()
        on_progress(f"Deleting {self.path}...")
        shutil.rmtree(self.path)
        on_progress(f"\n'{self.config.name}' removed from bench.")

    def _uninstall_from_all_sites(self, force: bool, on_progress: Callable[[str], None]) -> None:
        for site in self.bench.sites():
            if self.config.name not in site.list_apps():
                continue
            on_progress(f"Uninstalling '{self.config.name}' from site '{site.config.name}'...")
            try:
                site.uninstall_app(self, force=force)
            except Exception as e:
                if not force:
                    raise
                on_progress(f"Warning: could not cleanly uninstall from '{site.config.name}': {e}")

    def _deregister(self) -> None:
        apps_txt = self.bench.sites_path / "apps.txt"
        if not apps_txt.exists():
            return
        lines = [
            line for line in apps_txt.read_text().splitlines() if line.strip() != self.config.name
        ]
        apps_txt.write_text("\n".join(lines) + ("\n" if lines else ""))

    def _pip_uninstall(self) -> None:
        from pilot.managers.python_environment import PythonEnvManager

        PythonEnvManager(self.bench).uninstall_app(self.config.name)
