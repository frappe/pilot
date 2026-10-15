from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from pilot.config import RedisConfig
from pilot.exceptions import BenchError
from pilot.managers.packages import get_package_manager
from pilot.managers.platform import is_macos, which
from pilot.utils import is_port_listening

if TYPE_CHECKING:
    from pilot.core.bench import Bench


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

    @property
    def servers(self) -> dict[str, int]:
        """Config filename -> port, one per Redis server the bench runs."""
        return {"redis_cache.conf": self.config.cache_port, "redis_queue.conf": self.config.queue_port}

    def generate_configs(self) -> None:
        for filename, port in self.servers.items():
            self._write_config(filename, port)

    @contextmanager
    def running(self) -> Iterator[None]:
        """Serve the bench's Redis for the duration, for commands such as migrate
        that need it while the bench is stopped. Servers already up are left alone."""
        processes: list[subprocess.Popen] = []
        try:
            for name, port in self.servers.items():
                if is_port_listening(port):
                    continue
                processes.append(
                    subprocess.Popen(
                        [redis_server_binary() or "redis-server", str(self.bench.config_path / name)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                )
                wait_until_serving(port)
            yield
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                process.wait(timeout=10)

    def _write_config(self, filename: str, port: int) -> None:
        self.bench.config_path.mkdir(parents=True, exist_ok=True)
        content = f'port {port}\nbind 127.0.0.1\nsave ""\n'
        (self.bench.config_path / filename).write_text(content)


def wait_until_serving(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not is_port_listening(port):
        if time.monotonic() > deadline:
            raise BenchError(f"Redis did not start on port {port} within {timeout:.0f}s.")
        time.sleep(0.1)
