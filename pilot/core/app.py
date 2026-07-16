from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pilot.config.app_config import AppConfig
from pilot.exceptions import BenchError, CommandError
from pilot.utils import installed_app_version, run_command

if TYPE_CHECKING:
    from pilot.core.bench import Bench


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


class App:
    def __init__(self, config: AppConfig, bench: "Bench") -> None:
        self.config = config
        self.bench = bench

    @property
    def path(self) -> Path:
        return self.bench.apps_path / self.config.name

    @property
    def installed_version(self) -> str:
        """The version pip installed for this app, read from dist-info metadata."""
        return installed_app_version(self.bench.env_path, self.config.name)

    @property
    def installed_hash(self) -> str:
        """Full SHA of the app's current HEAD, or '' if it can't be resolved."""
        import subprocess

        result = subprocess.run(
            ["git", "-C", str(self.path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    @property
    def installed_tag(self) -> str:
        """Tag checked out exactly at HEAD, or '' if HEAD isn't on a tag."""
        import subprocess

        result = subprocess.run(
            ["git", "-C", str(self.path), "describe", "--tags", "--exact-match"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

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
        import subprocess

        branch = self.config.branch
        if not branch:
            return False
        result = subprocess.run(
            ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
            cwd=str(self.path),
            capture_output=True,
            text=True,
            timeout=15,
        )
        for line in result.stdout.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                remote_sha = parts[0].strip()
                local_sha = self.installed_hash
                return bool(remote_sha and local_sha and remote_sha != local_sha)
        return False

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
    def _git_remote(self) -> tuple[str, dict | None]:
        from pilot.integrations.git import git_remote_for

        return git_remote_for(self.bench.path, self.config.repo)

    def _detect_default_branch(self) -> str:
        import subprocess

        remote, env = self._git_remote
        result = subprocess.run(
            ["git", "ls-remote", "--symref", remote, "HEAD"],
            capture_output=True,
            text=True,
            env=env,
        )
        for line in result.stdout.splitlines():
            if line.startswith("ref: refs/heads/"):
                return line.split("refs/heads/")[1].split()[0]
        # Probe common Frappe branch names in priority order
        refs = subprocess.run(
            ["git", "ls-remote", "--heads", remote],
            capture_output=True,
            text=True,
            env=env,
        ).stdout
        for candidate in ("develop", "master", "version-16", "version-15"):
            if f"refs/heads/{candidate}" in refs:
                return candidate
        return "develop"

    def is_commit_hash(self, ref: str) -> bool:
        import re

        return bool(re.fullmatch(r"[0-9a-f]{7,40}", ref))

    def _clone_rev(self, commit: str) -> None:
        remote, env = self._git_remote
        run_command(["git", "clone", remote, str(self.path)], env=env, stream_output=True)
        try:
            run_command(["git", "-C", str(self.path), "checkout", commit])
        except CommandError:
            raise BenchError(f"Commit '{commit}' not found in {self.config.repo}.")

    def clone(self) -> None:
        target = self.config.branch or self._detect_default_branch()
        remote, env = self._git_remote
        if self.is_commit_hash(target):
            self._clone_rev(target)
        else:
            run_command(
                ["git", "clone", remote, "--branch", target, "--depth", "1", str(self.path)],
                env=env,
                stream_output=True,
            )

    @property
    def _is_shallow(self) -> bool:
        import subprocess

        result = subprocess.run(
            ["git", "-C", str(self.path), "rev-parse", "--is-shallow-repository"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() == "true"

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
