from __future__ import annotations

import re
from pathlib import Path

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.exceptions import CommandError
from pilot.managers.platform import which
from pilot.managers.redis import RedisManager
from pilot.utils import run_command


class OSProvider:
    """Host/runtime facts for the admin API."""

    def __init__(self, bench_root: Path, config: BenchConfig) -> None:
        self._bench_root = bench_root
        self._config = config

    def get_versions(self) -> dict[str, str]:
        versions = {
            "Python": self._config.python_version,
            "Node": self.get_flag_version("node", ["--version"], r"v?([\d.]+)"),
            "MariaDB": self.get_flag_version("mariadbd", ["--version"], r"Ver ([\d.]+)") or "",
            "PostgreSQL": self.get_flag_version("psql", ["--version"], r"(\d+\.\d+)"),
            "Redis": RedisManager.installed_version() or self._config.redis.version or "",
            "Nginx": self.get_flag_version("nginx", ["-v"], r"nginx/([\d.]+)"),
            "Frappe": self.frappe_version,
        }
        return {label: value for label, value in versions.items() if value}

    def get_flag_version(self, binary: str, args: list[str], pattern: str) -> str:
        if which(binary) is None:
            return ""

        try:
            result = run_command([binary, *args], timeout=5)
        except (OSError, CommandError):
            return ""

        output = (result.stdout or result.stderr or b"").decode(errors="replace")
        match = re.search(pattern, output)
        return match.group(1) if match else ""

    @property
    def frappe_version(self) -> str:
        bench = Bench(self._config, self._bench_root)
        if not bench.python.exists():
            return ""

        try:
            result = run_command([*bench.python_argv, "-c", "import frappe; print(frappe.__version__)"])
        except CommandError:
            return ""

        return result.stdout.decode().strip()

