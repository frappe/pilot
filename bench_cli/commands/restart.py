from __future__ import annotations

from bench_cli.core.bench import Bench
from bench_cli.managers.process_manager import ProcessManagerFactory


class RestartCommand:
    def __init__(self, bench: Bench) -> None:
        self.bench = bench

    def run(self) -> None:
        manager = ProcessManagerFactory.detect_running(self.bench)
        manager.generate_config()
        manager.reload()
        manager.restart()
