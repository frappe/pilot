from __future__ import annotations

from bench_cli.core.bench import Bench
from bench_cli.exceptions import BenchError


class RestartCommand:
    def __init__(self, bench: Bench) -> None:
        self.bench = bench

    def run(self) -> None:
        from bench_cli.managers.supervisor_process_manager import SupervisorProcessManager
        manager = SupervisorProcessManager(self.bench)
        if not manager.supervisor_conf_path.exists():
            raise BenchError(
                "Supervisor config not found. Run 'bench setup production' first."
            )
        manager.generate_config()
        manager.reload()
        manager.restart()
