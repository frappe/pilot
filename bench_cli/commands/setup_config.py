from __future__ import annotations

from bench_cli.core.bench import Bench
from bench_cli.managers.nginx_manager import NginxManager
from bench_cli.managers.process_manager import ProcessManagerFactory
from bench_cli.managers.redis_manager import RedisManager


class UpdateConfigCommand:
    def __init__(self, bench: Bench) -> None:
        self.bench = bench

    def run(self) -> None:
        print("Updating Redis configs...")
        RedisManager(self.bench.config.redis, self.bench).generate_configs()

        print("Updating process manager config...")
        ProcessManagerFactory.create(self.bench).generate_config()

        print("Updating common_site_config.json...")
        self.bench.write_common_site_config()

        if self.bench.config.production.nginx:
            print("Updating nginx configs...")
            NginxManager(self.bench).generate_config()
            print("  Note: run 'bench setup nginx' to reload nginx with the new config.")
