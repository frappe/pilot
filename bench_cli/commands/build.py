from __future__ import annotations

from bench_cli.core.bench import Bench
from bench_cli.managers.process_manager import ProcessManagerFactory
from bench_cli.managers.python_env_manager import PythonEnvManager


class BuildCommand:
    def __init__(self, bench: Bench, force: bool = False) -> None:
        self.bench = bench
        self.force = force

    def run(self) -> None:
        manager = PythonEnvManager(self.bench)
        if self.force:
            manager.build_assets()
        else:
            for app in self.bench.apps():
                manager.build_assets_for_app(app)
        ProcessManagerFactory.create(self.bench).reload_web()
