from __future__ import annotations

from bench_cli.core.bench import Bench
from bench_cli.exceptions import BenchError
from bench_cli.managers.process_manager import ProcessManagerFactory


class RestartCommand:
    def __init__(self, bench: Bench) -> None:
        self.bench = bench

    def run(self) -> None:
        if not self.bench.config.production.nginx:
            raise BenchError("'bench restart' is only available in production mode. Use 'bench stop' and 'bench start' in dev mode.")
        from bench_cli.managers.supervisor_process_manager import SupervisorProcessManager
        manager = ProcessManagerFactory.create(self.bench)
        assert isinstance(manager, SupervisorProcessManager)
        if not manager.supervisor_conf_path.exists():
            raise BenchError("Supervisor config not found. Run 'bench setup production' first.")
        manager.generate_config()
        manager.reload()
        manager.restart()
