from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from bench_cli.config.app_config import AppConfig
from bench_cli.exceptions import BenchError, CommandError
from bench_cli.utils import run_command

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class App:
    def __init__(self, config: AppConfig, bench: "Bench") -> None:
        self.config = config
        self.bench = bench

    @property
    def path(self) -> Path:
        return self.bench.apps_path / self.config.name

    @property
    def is_cloned(self) -> bool:
        return self.path.exists() and (self.path / ".git").exists()

    @property
    def _remote_url(self) -> str:
        """The clone URL to use, token-embedded when the repo is private.

        Public repos resolve to the original URL; for a repo hosted on a
        connected provider with a stored PAT, the token is injected so private
        clones and ls-remote probes authenticate.
        """
        from bench_cli.core.git_providers import authenticated_url_for

        return authenticated_url_for(self.bench.path, self.config.repo)

    def _detect_default_branch(self) -> str:
        import subprocess

        remote = self._remote_url
        result = subprocess.run(
            ["git", "ls-remote", "--symref", remote, "HEAD"],
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            if line.startswith("ref: refs/heads/"):
                return line.split("refs/heads/")[1].split()[0]
        # Probe common Frappe branch names in priority order
        refs = subprocess.run(
            ["git", "ls-remote", "--heads", remote],
            capture_output=True,
            text=True,
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
        branch = self.config.branch or self._detect_default_branch()
        if self.is_commit_hash(branch):
            self._clone_rev(branch)

        else:
            run_command(
                [
                    "git",
                    "clone",
                    self._remote_url,
                    "--branch",
                    branch,
                    "--depth",
                    "1",
                    str(self.path),
                ],
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

    def update(self) -> None:
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
