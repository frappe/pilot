from __future__ import annotations

import contextlib
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config import AppConfig
from pilot.core.app.install_result import AppInstallResult
from pilot.core.app.repository import AppRepository
from pilot.core.app.revisions import RevisionPin
from pilot.exceptions import BenchError
from pilot.utils import installed_app_version, run_command

if TYPE_CHECKING:
    from pilot.core.app.requirements import AppRequirements
    from pilot.core.bench import Bench


@dataclass
class NewAppOptions:
    """Answers fed to `make-app`'s prompts. Empty title/license/branch accept
    frappe's defaults; description, publisher and email must be non-empty."""

    title: str = ""
    description: str = ""
    publisher: str = ""
    email: str = ""
    license: str = ""
    branch: str = ""
    github_workflow: bool = False

    def as_answers(self) -> str:
        answers = [
            self.title,
            self.description,
            self.publisher,
            self.email,
            self.license,
            "y" if self.github_workflow else "n",
            self.branch,
        ]
        return "\n".join(answers) + "\n"


class App:
    def __init__(self, config: AppConfig, bench: "Bench", *, staged: bool = False) -> None:
        self.config = config
        self.bench = bench
        self.is_staged = staged  # cloned outside apps/, pending validation

    @classmethod
    def from_repo(cls, bench: "Bench", repo: str, branch: str = "") -> "App":
        """Create an App from a git URL, rejecting the framework app."""
        from pathlib import PurePosixPath

        name = PurePosixPath(repo.rstrip("/")).name
        if name.endswith(".git"):
            name = name[:-4]
        if name.replace("-", "_").lower() == "frappe":
            raise BenchError(
                "'frappe' is the base framework, not an app - it can't be added "
                "with get-app. It's set up when the bench itself is created."
            )
        return cls(AppConfig(name=name, repo=repo, branch=branch), bench)

    @classmethod
    def scaffold(
        cls,
        bench: "Bench",
        app_name: str,
        options: "NewAppOptions | None" = None,
        *,
        on_progress: Callable[[str], None] = lambda message: None,
    ) -> "App":
        """Create a new Frappe app under apps/ via `make-app`, then install it.
        With `options`, `make-app`'s prompts are answered from it; without, they
        run interactively against the terminal."""
        name = cls._normalize_new_app_name(app_name)
        if not cls.is_available_on_bench(bench, app_name):
            raise BenchError(f"App '{name}' already exists in this bench.")

        args = [*bench.frappe_call, "frappe", "make-app", str(bench.apps_path), name]
        on_progress(f"Creating new app '{name}'...")
        # Answers are piped, so keep make-app quiet - its echoed prompts are noise.
        run_command(
            args,
            cwd=bench.sites_path,
            stream_output=options is None,
            stdin_text=options.as_answers() if options else None,
        )

        app = cls(AppConfig(name=name, repo="", branch=options.branch if options else ""), bench)
        on_progress(f"Installing '{name}'...")
        try:
            app._install_into_environment()
            app._register()
        except Exception:
            shutil.rmtree(app.path, ignore_errors=True)
            raise
        on_progress(f"\n'{name}' created and installed successfully.")
        return app

    @classmethod
    def is_available_on_bench(cls, bench: "Bench", app_name: str) -> bool:
        """Whether `app_name` is free to create - not registered and not on disk."""
        name = cls._normalize_new_app_name(app_name)
        return not bench.is_app_installed(name) and not (bench.apps_path / name).exists()

    @staticmethod
    def _normalize_new_app_name(app_name: str) -> str:
        name = app_name.strip().lower().replace(" ", "_").replace("-", "_")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise BenchError(
                "App name must start with a letter and contain only lowercase "
                "letters, numbers, and underscores."
            )
        return name

    @property
    def path(self) -> Path:
        root = self.bench.staging_path if self.is_staged else self.bench.apps_path
        return root / self.config.name

    @property
    def requirements(self) -> "AppRequirements":
        from pilot.core.app.requirements import AppRequirements

        return AppRequirements(self)

    @property
    def installed_version(self) -> str:
        """The version pip installed for this app, read from dist-info metadata."""
        return installed_app_version(self.bench.env_path, self.config.name)

    @property
    def _repository(self) -> AppRepository:
        return AppRepository(self)

    @property
    def installed_hash(self) -> str:
        return self._repository.installed_hash

    @property
    def installed_tag(self) -> str:
        return self._repository.installed_tag

    @property
    def head_sha(self) -> str:
        """The commit checked out on disk, unlike installed_hash which reads dist-info."""
        return self._repository.repo.head_sha

    @property
    def current_branch(self) -> str:
        """The branch checked out on disk, empty when HEAD is detached."""
        return self._repository.repo.branch

    def is_on_revision(self, pin: RevisionPin) -> bool:
        return self._repository.is_on_revision(pin)

    def has_marketplace_update(self) -> bool:
        return self._repository.has_marketplace_update()

    def update_target(self) -> RevisionPin | None:
        return self._repository.update_target()

    @property
    def marketplace_entry(self) -> dict | None:
        return self._repository.marketplace_entry

    @property
    def is_marketplace(self) -> bool:
        return self.marketplace_entry is not None

    @property
    def existing_clone_path(self) -> Path | None:
        by_config_name = self.bench.apps_path / self.config.name
        if (by_config_name / ".git").exists():
            return by_config_name
        by_module_name = self.bench.apps_path / self.module_name
        if by_module_name != by_config_name and (by_module_name / ".git").exists():
            return by_module_name
        return None

    @property
    def is_cloned(self) -> bool:
        if (self.path / ".git").exists():
            return True
        return self.existing_clone_path is not None

    def is_commit_hash(self, ref: str) -> bool:
        return AppRepository.is_commit_hash(ref)

    def clone(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        """Clone into apps/, or into staging when this app is staged - nothing
        unvalidated should sit in apps/ where a bench-wide operation would pick it
        up. A tree already in apps/ is moved into staging rather than cloned again."""
        if not self.is_staged:
            on_progress(f"Cloning {self.config.name}...")
            self._repository.clone()
            return

        shutil.rmtree(self.path, ignore_errors=True)  # leftovers from an interrupted run
        self.path.parent.mkdir(parents=True, exist_ok=True)
        apps_clone = self.existing_clone_path
        if apps_clone is not None:
            on_progress(f"'{self.config.name}' already cloned, moving it out of apps/ to validate.")
            shutil.move(str(apps_clone), str(self.path))
            return

        on_progress(f"Cloning {self.config.name}...")
        self._repository.clone()

    def update(self, pin: RevisionPin | None = None) -> None:
        self._repository.update(pin)

    def switch_branch(self, branch: str) -> None:
        self._repository.switch_branch(branch)

    def checkout_commit(self, sha: str) -> None:
        """Check out a specific commit SHA, refetching it from origin if needed."""
        self._repository.checkout_pinned_commit(sha)

    def _pyproject(self) -> dict:
        """Parsed pyproject.toml, or an empty dict when it is missing or malformed."""
        import tomllib

        try:
            return tomllib.loads((self.path / "pyproject.toml").read_text())
        except (tomllib.TOMLDecodeError, OSError):
            return {}

    @property
    def has_dev_extra(self) -> bool:
        """Whether pyproject declares a `dev` extra, such as frappe's watchdog group."""
        return "dev" in self._pyproject().get("project", {}).get("optional-dependencies", {})

    @property
    def editable_target(self) -> str:
        """Target for `uv pip install -e`. A dev bench also pulls the app's dev extra,
        which is where frappe keeps watchdog, the reloader `--dev` refuses to start
        without."""
        if self.bench.config.production.enabled or not self.has_dev_extra:
            return str(self.path)
        return f"{self.path}[dev]"

    @property
    def module_name(self) -> str:
        """Return the importable package name, preferring pyproject.toml."""
        name = self._pyproject().get("project", {}).get("name")
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

    def _skip_already_installed(
        self, on_progress: Callable[[str], None], install_dependencies: bool = False
    ) -> AppInstallResult:
        app = self.bench.app(self.module_name)
        dependencies = app._install_dependencies(on_progress) if install_dependencies else []
        on_progress(f"'{app.config.name}' already installed, skipping.")
        return AppInstallResult(app, already_installed=True, installed_dependencies=dependencies)

    def install(
        self,
        *,
        install_dependencies: bool = False,
        commit: str = "",
        on_progress: Callable[[str], None] = lambda message: None,
    ) -> AppInstallResult:
        """Pinned commit based Clone, validate, install, register, and build app assets."""
        if self.bench.is_app_installed(self.config.name):
            return self._skip_already_installed(on_progress, install_dependencies)

        existing_clone = self.existing_clone_path
        self.is_staged = not self.is_marketplace
        if self.is_staged or existing_clone is None:
            self.clone(on_progress)

        try:
            if commit and self.installed_hash != commit:
                on_progress(f"Checking out {self.config.name} at {commit[:8]}...")
                self.checkout_commit(commit)
            dependencies = self._install_dependencies(on_progress) if install_dependencies else []
            if self.is_staged:
                self.validate()
            self.promote()
        except BenchError:
            self._undo_clone(existing_clone)
            raise

        on_progress(f"Installing {self.config.name}...")

        try:
            self._install_into_environment()
            self._register()
            on_progress(f"\nSetting up assets for {self.config.name}...")
            self._build_assets_via_env_manager()
        except Exception:
            self._roll_back_install(delete_clone=existing_clone is None, on_progress=on_progress)
            raise

        self.record_branch()
        on_progress(f"\n'{self.config.name}' installed successfully.")
        return AppInstallResult(self, already_installed=False, installed_dependencies=dependencies)

    def _undo_clone(self, existing_clone: Path | None) -> None:
        """Undo the clone after a failure: delete a tree this run created, and put
        one that was already in apps/ back where it was. A working tree we did not
        create is not ours to remove."""
        if existing_clone is None:
            shutil.rmtree(self.path, ignore_errors=True)
        elif self.is_staged:
            shutil.move(str(self.path), str(existing_clone))

    def promote(self) -> "App":
        if not self.path.is_dir():
            raise BenchError(f"'{self.config.name}' was never cloned into {self.path} - nothing to install.")
        module = self.module_name
        target = self.bench.apps_path / module
        if target == self.path:
            return self
        if target.exists():
            raise BenchError(f"'{target}' already exists - remove it before installing this app.")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(self.path), str(target))  # a rename, unless apps/ is another filesystem
        self.config.name = module
        self.is_staged = False
        return self

    def _roll_back_install(self, *, delete_clone: bool, on_progress: Callable[[str], None]) -> None:
        """Undo a half-finished install - a registered but broken app takes the site
        down. Best effort: the caller re-raises the failure that got us here."""
        on_progress(f"\nInstall of '{self.config.name}' failed - undoing it...")
        with contextlib.suppress(Exception):
            self._deregister()
        with contextlib.suppress(Exception):
            self._pip_uninstall()
        if delete_clone:
            shutil.rmtree(self.path, ignore_errors=True)

    def _install_dependencies(self, on_progress: Callable[[str], None]) -> list["App"]:
        from pilot.core.app.dependency_installer import AppDependencyInstaller

        return AppDependencyInstaller(self.bench, self).install(on_progress)

    def validate(self) -> None:
        from pilot.core.app.validator import Validator

        Validator(self).validate()

    def _install_into_environment(self) -> None:
        from pilot.managers.environment import PythonEnvManager

        PythonEnvManager(self.bench).install_app(self)

    def _register(self) -> None:
        existing = self.bench.registered_apps()
        if self.config.name not in existing:
            (self.bench.sites_path / "apps.txt").write_text("\n".join([*existing, self.config.name]) + "\n")

    def record_branch(self) -> None:
        """Persist this app's tracked branch to bench.toml so it survives a
        detached HEAD after a later commit pin (see BenchInventory._configured_branch)."""
        if not self.config.branch or self.is_commit_hash(self.config.branch):
            return
        from pilot.config import BenchConfig

        if not BenchConfig.toml_path(self.bench.path).exists():
            return
        with BenchConfig.open(self.bench.path, mode="raw") as raw:
            apps = raw.setdefault("apps", [])
            entry = next((a for a in apps if a.get("name") == self.config.name), None)
            if entry is None:
                apps.append(
                    {"name": self.config.name, "repo": self.config.repo, "branch": self.config.branch}
                )
            else:
                entry["branch"] = self.config.branch

    def _build_assets_via_env_manager(self) -> None:
        from pilot.managers.environment import PythonEnvManager

        PythonEnvManager(self.bench).build_assets_for_app(self)

    def ensure_removable(self) -> None:
        if not self.path.exists():
            raise BenchError(f"App '{self.config.name}' not found in bench.")
        framework = self.bench.config.framework_app.name
        if self.config.name == framework:
            raise BenchError(f"Cannot remove the framework app '{framework}'.")

    def remove(self, force: bool = False, on_progress: Callable[[str], None] = lambda message: None) -> None:
        """Uninstall from sites, deregister, pip-uninstall, and delete the clone."""
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
        lines = [line for line in apps_txt.read_text().splitlines() if line.strip() != self.config.name]
        apps_txt.write_text("\n".join(lines) + ("\n" if lines else ""))

    def _pip_uninstall(self) -> None:
        from pilot.managers.environment import PythonEnvManager

        PythonEnvManager(self.bench).uninstall_app(self.config.name)
