from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pilot.config.app import AppConfig
from pilot.exceptions import BenchError, CommandError
from pilot.utils import installed_app_version, run_command

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.internal.git import GitRepo


@dataclass(frozen=True)
class RevisionPin:
    """A fixed revision (tag or commit) an app should be checked out at.

    Keeps App's public methods decoupled from the shape of any particular
    source's data (e.g. the marketplace registry's raw target dicts) —
    callers translate their own data into this before calling into App.
    A branch is not a fixed revision, so it has no RevisionPin; pass None
    to mean "no pin, follow the tracked branch" instead.
    """

    kind: Literal["tag", "commit"]
    ref: str

    @classmethod
    def from_marketplace_target(cls, target: dict) -> "RevisionPin | None":
        """Build a pin from a registry target dict, or None for a branch target."""
        kind = target.get("target_type")
        if kind not in ("tag", "commit"):
            return None
        return cls(kind=kind, ref=target["target"])


@dataclass(frozen=True)
class AppInstallResult:
    """Outcome of installing an app: the final App (renamed to its importable
    module name if that differs from the requested one), whether it was
    already installed, and any dependency Apps installed alongside it."""

    app: "App"
    already_installed: bool
    installed_dependencies: list["App"]


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
        from pilot.internal.git import GitRepo

        return GitRepo(self.path)

    @property
    def installed_hash(self) -> str:
        """Full SHA of the app's current HEAD, or '' if it can't be resolved."""
        return self._repo.head_sha

    @property
    def installed_tag(self) -> str:
        """Tag checked out exactly at HEAD, or '' if HEAD isn't on a tag."""
        return self._repo.tag_at_head

    def is_on_revision(self, pin: RevisionPin) -> bool:
        """Whether this app is currently checked out at a pinned revision."""
        if pin.kind == "tag":
            return self.installed_tag == pin.ref
        
        hash = self.installed_hash
        return bool(hash) and hash.startswith(pin.ref)

    def has_remote_update(self) -> bool:
        """Whether the tracked branch has commits on origin not yet pulled locally.

        Runs `git ls-remote` — ref pointers only, no object download — so it
        completes in ~1-2s regardless of repo size. An app not on a branch
        (detached HEAD, e.g. a tag/commit checkout) has no moving remote tip
        to compare against, so this always reports no update; use
        `is_on_revision` against the marketplace target instead.
        """
        if not self.config.branch:
            return False
        remote_sha = self._repo.remote_branch_sha(self.config.branch)
        return bool(remote_sha and self.installed_hash and remote_sha != self.installed_hash)

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
        """The clone URL to use, token-embedded when the repo is private.

        Public repos resolve to the original URL; for a repo hosted on a
        connected provider with a stored PAT, the token is injected so private
        clones and ls-remote probes authenticate.
        """
        from pilot.integrations.git import authenticated_url_for

        return authenticated_url_for(self.bench.path, self.config.repo)

    def _detect_default_branch(self) -> str:
        import subprocess

        remote = self._remote_url
        result = subprocess.run(
            ["git", "ls-remote", "--symref", remote, "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.splitlines():
            if line.startswith("ref: refs/heads/"):
                return line.split("refs/heads/")[1].split()[0]
        # Probe common Frappe branch names in priority order
        refs = subprocess.run(
            ["git", "ls-remote", "--heads", remote],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        for candidate in ("develop", "master", "version-16", "version-15"):
            if f"refs/heads/{candidate}" in refs:
                return candidate
        return "develop"

    def is_commit_hash(self, ref: str) -> bool:
        import re

        return bool(re.fullmatch(r"[0-9a-f]{7,40}", ref))

    def _clone_rev(self, commit: str) -> None:
        run_command(["git", "clone", self._remote_url, str(self.path)], stream_output=True)
        try:
            run_command(["git", "-C", str(self.path), "checkout", commit])
        except CommandError:
            raise BenchError(f"Commit '{commit}' not found in {self.config.repo}.")

    def clone(self) -> None:
        target = self.config.branch or self._detect_default_branch()
        if self.is_commit_hash(target):
            self._clone_rev(target)
        else:
            run_command(
                ["git", "clone", self._remote_url, "--branch", target, "--depth", "1", str(self.path)],
                stream_output=True,
            )

    @property
    def _is_shallow(self) -> bool:
        return self._repo.is_shallow

    @staticmethod
    def _pack_threads() -> int:
        import os

        cpus = os.cpu_count() or 1
        # On constrained servers (≤2 vCPUs) cap at 1 to avoid saturating the CPU.
        # On beefier machines let git use half the cores so other processes stay responsive.
        if cpus <= 2:
            return 1
        return max(1, cpus // 2)

    def update(self, pin: RevisionPin | None = None) -> None:
        """Pull the latest code.

        If `pin` is given, the app is moved to exactly that revision — not
        the branch tip or the repo's overall latest tag/commit. A pin's
        source (e.g. the marketplace registry) only ever advances, so no
        ancestry check is needed here; it's the source of truth. Otherwise
        this pulls the tracked branch's tip, as before.
        """
        if pin is not None:
            self._checkout_pinned_target(pin)
            return

        cmd = ["git", "-c", f"pack.threads={self._pack_threads()}", "-C", str(self.path), "fetch", "origin", self.config.branch]
        if self._is_shallow:
            cmd.append("--depth=1")
        run_command(cmd)
        run_command(
            [
                "git",
                "-C",
                str(self.path),
                "reset",
                "--hard",
                f"origin/{self.config.branch}",
            ]
        )

    def _checkout_pinned_target(self, pin: RevisionPin) -> None:
        if pin.kind == "tag":
            run_command(["git", "-C", str(self.path), "fetch", "--depth", "1", "origin", pin.ref])
            run_command(["git", "-C", str(self.path), "checkout", "FETCH_HEAD"])
        else:
            self._checkout_pinned_commit(pin.ref)

    def _checkout_pinned_commit(self, sha: str) -> None:
        """Check out a specific commit SHA."""
        try:
            run_command(["git", "-C", str(self.path), "fetch", "--depth", "1", "origin", sha])
            run_command(["git", "-C", str(self.path), "checkout", "FETCH_HEAD"])
            return
        except CommandError:
            pass
        unshallow_flag = ["--unshallow"] if self._is_shallow else []
        run_command(["git", "-C", str(self.path), "fetch", *unshallow_flag, "origin", self.config.branch])
        run_command(["git", "-C", str(self.path), "checkout", sha])

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
            return AppInstallResult(app, already_installed=True, installed_dependencies=dependencies)

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
        renamed = App(AppConfig(name=module, repo=self.config.repo, branch=self.config.branch), self.bench)
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
            (self.bench.sites_path / "apps.txt").write_text("\n".join(existing + [self.config.name]) + "\n")

    def _build_assets_via_env_manager(self) -> None:
        from pilot.managers.python_environment import PythonEnvManager

        PythonEnvManager(self.bench).build_assets_for_app(self)

    def ensure_removable(self) -> None:
        if not self.path.exists():
            raise BenchError(f"App '{self.config.name}' not found in bench.")
        framework = self.bench.config.framework_app.name
        if self.config.name == framework:
            raise BenchError(f"Cannot remove the framework app '{framework}'.")

    def remove(self, force: bool = False, on_progress: Callable[[str], None] = lambda message: None) -> None:
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
        lines = [line for line in apps_txt.read_text().splitlines() if line.strip() != self.config.name]
        apps_txt.write_text("\n".join(lines) + ("\n" if lines else ""))

    def _pip_uninstall(self) -> None:
        from pilot.managers.python_environment import PythonEnvManager

        PythonEnvManager(self.bench).uninstall_app(self.config.name)
