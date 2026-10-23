from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config import RedisConfig
from pilot.exceptions import ConfigError
from pilot.managers.packages import get_package_manager
from pilot.managers.platform import is_macos, which

if TYPE_CHECKING:
    from pilot.core.bench import Bench

_BLOCK_START = "# BEGIN PILOT MANAGED BLOCK. Pilot rewrites this block; add your settings outside it."
_BLOCK_END = "# END PILOT MANAGED BLOCK"
_PILOT_DIRECTIVE = re.compile(r"^(port|bind|save)\b")


def redis_server_binary() -> str | None:
    """Return redis-server or valkey-server when installed."""
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
        path = self.bench.config_path / filename
        before, after = self._split_custom_config(path)
        block = f'{_BLOCK_START}\nport {port}\nbind 127.0.0.1\nsave ""\n{_BLOCK_END}\n'
        path.write_text(before + block + after)

    @staticmethod
    def _split_custom_config(path: Path) -> tuple[str, str]:
        """The lines before and after Pilot's block. A file without the block keeps all but Pilot's directives."""
        if not path.exists():
            return "", ""
        text = path.read_text()
        before, start, rest = text.partition(_BLOCK_START)
        if not start:
            return "", "".join(line for line in text.splitlines(keepends=True) if not _PILOT_DIRECTIVE.match(line))
        _, end, after = rest.partition(_BLOCK_END)
        if not end:
            raise ConfigError(f"{path} has no '{_BLOCK_END}' line. Restore it or delete the file.")
        return before, after.removeprefix("\n")
