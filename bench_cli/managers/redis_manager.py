from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from bench_cli.config.redis_config import RedisConfig
from bench_cli.platform import get_package_manager, is_macos

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class RedisManager:
    def __init__(self, config: RedisConfig, bench: "Bench") -> None:
        self.config = config
        self.bench = bench

    def is_installed(self) -> bool:
        return shutil.which("redis-server") is not None

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
        if self.config.is_single_instance:
            self._write_single_config()
        else:
            self._write_cache_config()
            self._write_queue_config()
            self._write_socketio_config()

    def _write_single_config(self) -> None:
        content = (
            f"port {self.config.cache_port}\n"
            "bind 127.0.0.1\n"
            'save ""\n'
        )
        (self.bench.config_path / "redis.conf").write_text(content)

    def _write_cache_config(self) -> None:
        content = (
            f"port {self.config.cache_port}\n"
            "bind 127.0.0.1\n"
            'save ""\n'
        )
        (self.bench.config_path / "redis_cache.conf").write_text(content)

    def _write_queue_config(self) -> None:
        content = (
            f"port {self.config.queue_port}\n"
            "bind 127.0.0.1\n"
            'save ""\n'
        )
        (self.bench.config_path / "redis_queue.conf").write_text(content)

    def _write_socketio_config(self) -> None:
        content = (
            f"port {self.config.socketio_port}\n"
            "bind 127.0.0.1\n"
        )
        (self.bench.config_path / "redis_socketio.conf").write_text(content)
