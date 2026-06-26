from __future__ import annotations

from typing import TYPE_CHECKING

from pilot.commands.base import Command

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class UpdateConfigCommand(Command):
    name = "config"
    help = "Regenerate config files from bench.toml."
    group = "setup"

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def run(self) -> None:
        from pilot.managers.nginx_manager import NginxManager
        from pilot.managers.process_manager import ProcessManagerFactory
        from pilot.managers.redis_manager import RedisManager

        print("Updating Redis configs...")
        RedisManager(self.bench.config.redis, self.bench).generate_configs()

        print("Updating process manager config...")
        ProcessManagerFactory.create(self.bench).generate_config()

        print("Updating common_site_config.json...")
        self.bench.write_common_site_config()

        if self.bench.config.production.enabled:
            print("Updating nginx configs...")
            NginxManager(self.bench).generate_config()
            print("  Note: run 'bench setup nginx' to reload nginx with the new config.")
