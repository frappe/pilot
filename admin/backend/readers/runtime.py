from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pilot.config.bench_config import BenchConfig
from pilot.exceptions import CommandError
from pilot.managers.redis import RedisManager
from pilot.managers.platform import which
from pilot.utils import run_command


class RuntimeVersionReader:
    """Detects installed versions of the runtimes a bench depends on.

    Anything not installed (or not detectable) is left out of the result
    rather than reported as an error, since not every host runs every
    service (e.g. postgres may be absent)."""

    def __init__(self, bench_root: Path, config: BenchConfig) -> None:
        self._bench_root = bench_root
        self._config = config

    def read(self) -> dict[str, str]:
        versions = {
            "Python": self._config.python_version,
            "Node": self._flag_version("node", ["--version"], r"v?([\d.]+)"),
            "MariaDB": self._flag_version("mariadbd", ["--version"], r"Ver ([\d.]+)") or "",
            "PostgreSQL": self._flag_version("psql", ["--version"], r"(\d+\.\d+)"),
            "Redis": RedisManager.installed_version() or self._config.redis.version or "",
            "Nginx": self._nginx_version(),
            "Frappe": self._frappe_version(),
            "Pilot": self._bench_admin_commit(),
        }
        return {label: value for label, value in versions.items() if value}

    def _flag_version(self, binary: str, args: list[str], pattern: str) -> str:
        if which(binary) is None:
            return ""
        try:
            result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return ""
        match = re.search(pattern, result.stdout or result.stderr)
        return match.group(1) if match else ""

    def _nginx_version(self) -> str:
        # nginx prints its version to stderr, not stdout, when passed -v.
        if which("nginx") is None:
            return ""
        try:
            result = subprocess.run(["nginx", "-v"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return ""
        match = re.search(r"nginx/([\d.]+)", result.stderr)
        return match.group(1) if match else ""

    def _frappe_version(self) -> str:
        python_bin = self._bench_root / "env" / "bin" / "python"
        if not python_bin.exists():
            return ""
        try:
            result = run_command([str(python_bin), "-c", "import frappe; print(frappe.__version__)"])
        except CommandError:
            return ""
        return result.stdout.decode().strip()

    def _bench_admin_commit(self) -> str:
        from pilot.loader import cli_root

        try:
            result = subprocess.run(
                ["git", "-C", str(cli_root()), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""
