from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

from pilot.config.redis_config import RedisConfig
from pilot.managers.packages import get_package_manager
from pilot.managers.platform import is_macos, which

if TYPE_CHECKING:
    from pilot.core.bench import Bench


def redis_server_binary() -> str | None:
    """Path to the redis server binary, or None if not installed.

    Fedora and Arch ship valkey (a redis fork) whose binary keeps redis'
    CLI and version-output format."""
    return which("redis-server") or which("valkey-server")


class RedisManager:
    def __init__(self, config: RedisConfig, bench: "Bench") -> None:
        self.config = config
        self.bench = bench

    def is_installed(self) -> bool:
        return redis_server_binary() is not None

    @staticmethod
    def installed_version() -> str:
        """Return the installed redis-server version (e.g. '7.0.11'), or '' if unavailable."""
        binary = redis_server_binary()
        if binary is None:
            return ""
        try:
            result = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        # Output: "Redis server v=7.0.11 sha=... malloc=... bits=64 build=..."
        match = re.search(r"v=(\S+)", result.stdout)
        return match.group(1) if match else ""

    def install(self) -> None:
        if self.is_installed():
            return
        package_manager = get_package_manager()
        package = self._brew_package() if is_macos() else "redis-server"
        package_manager.install(package)

    def _brew_package(self) -> str:
        if self.config.version:
            return f"redis@{self.config.version}"
        return "redis"

    def generate_configs(self) -> None:
        self._write_config("redis_cache.conf", self.config.cache_port)
        self._write_config("redis_queue.conf", self.config.queue_port)

    def _write_config(self, filename: str, port: int) -> None:
        self.bench.config_path.mkdir(parents=True, exist_ok=True)
        content = (
            f"port {port}\n"
            "bind 127.0.0.1\n"
            'save ""\n'
        )
        (self.bench.config_path / filename).write_text(content)
