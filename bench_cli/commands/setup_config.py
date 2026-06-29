from __future__ import annotations

from typing import TYPE_CHECKING

from bench_cli.commands.base import Command

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class UpdateConfigCommand(Command):
    name = "config"
    help = "Regenerate config files from bench.toml."
    group = "setup"

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def run(self) -> None:
        from bench_cli.managers.nginx_manager import NginxManager
        from bench_cli.managers.process_manager import ProcessManager
        from bench_cli.managers.redis_manager import RedisManager

        print("Updating Redis configs...")
        RedisManager(self.bench.config.redis, self.bench).generate_configs()

        print("Updating process manager config...")
        ProcessManager.for_bench(self.bench).write_config()

        print("Updating common_site_config.json...")
        self.bench.write_common_site_config()

        if self.bench.config.production.enabled:
            print("Updating nginx configs...")
            NginxManager(self.bench).generate_config()
            print("  Note: run 'bench setup nginx' to reload nginx with the new config.")
