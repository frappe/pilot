from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from bench_cli.config.app_config import AppConfig
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

    def _detect_default_branch(self) -> str:
        import subprocess
        result = subprocess.run(
            ["git", "ls-remote", "--symref", self.config.repo, "HEAD"],
            capture_output=True, text=True,
        )
        for line in result.stdout.splitlines():
            if line.startswith("ref: refs/heads/"):
                return line.split("refs/heads/")[1].split()[0]
        # Probe common Frappe branch names in priority order
        refs = subprocess.run(
            ["git", "ls-remote", "--heads", self.config.repo],
            capture_output=True, text=True,
        ).stdout
        for candidate in ("develop", "master", "version-16", "version-15"):
            if f"refs/heads/{candidate}" in refs:
                return candidate
        return "develop"

    def clone(self) -> None:
        branch = self.config.branch or self._detect_default_branch()
        run_command([
            "git", "clone",
            self.config.repo,
            "--branch", branch,
            "--depth", "1",
            str(self.path),
        ], stream_output=True)

    @property
    def _is_shallow(self) -> bool:
        import subprocess
        result = subprocess.run(
            ["git", "-C", str(self.path), "rev-parse", "--is-shallow-repository"],
            capture_output=True, text=True,
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
        cmd = ["git", "-c", f"pack.threads={self._pack_threads()}", "-C", str(self.path),
               "fetch", "origin", self.config.branch]
        if self._is_shallow:
            cmd.append("--depth=1")
        run_command(cmd)
        run_command([
            "git", "-C", str(self.path),
            "reset", "--hard", f"origin/{self.config.branch}",
        ])

    def build_assets(self) -> None:
        if not (self.path / "package.json").exists():
            return
        run_command(["yarn", "--cwd", str(self.path), "build"])
