from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from pilot.config import BenchConfig
from pilot.exceptions import BenchError, CommandError
from pilot.internal.command import run_command
from pilot.managers.platform import which
from pilot.managers.redis import redis_server_binary


class OSProvider:
    """Host/runtime facts for the admin API."""

    def __init__(self, bench_root: Path, config: BenchConfig) -> None:
        self._bench_root = bench_root
        self._config = config

    def get_versions(self) -> dict[str, str]:
        versions = {
            "Python": self._config.python_version,
            "Node": self.get_flag_version("node", ["--version"], r"v?([\d.]+)"),
            "MariaDB": self.get_flag_version("mariadbd", ["--version"], r"Ver ([\d.]+)"),
            "PostgreSQL": self.get_flag_version("psql", ["--version"], r"(\d+\.\d+)"),
            "Redis": self.get_flag_version(redis_server_binary() or "", ["--version"], r"v=(\S+)")
            or self._config.redis.version
            or "",
            "Nginx": self.get_flag_version("nginx", ["-v"], r"nginx/([\d.]+)"),
            "Frappe": self.frappe_version,
        }
        return {label: value for label, value in versions.items() if value}

    def get_flag_version(self, binary: str, args: list[str], pattern: str) -> str:
        path = which(binary) if binary else None
        if path is None:
            return ""
        try:
            modified = os.stat(path).st_mtime_ns
        except OSError:
            return ""
        return _binary_version(path, modified, tuple(args), pattern)

    @property
    def frappe_version(self) -> str:
        from pilot.core.bench import Bench

        try:
            return Bench(self._config, self._bench_root).app("frappe").installed_version
        except BenchError:
            return ""


@lru_cache(maxsize=32)
def _binary_version(path: str, modified_ns: int, args: tuple[str, ...], pattern: str) -> str:
    """Keyed on the binary's mtime, so an upgrade reads the new version and repeat requests spawn nothing."""
    try:
        result = run_command([path, *args], timeout=5)
    except (OSError, CommandError):
        return ""
    output = (result.stdout or result.stderr or b"").decode(errors="replace")
    match = re.search(pattern, output)
    return match.group(1) if match else ""
