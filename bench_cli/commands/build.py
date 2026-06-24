from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from bench_cli.commands.base import Command

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class BuildCommand(Command):
    name = "build"
    help = "Build assets (downloads pre-built if available)."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--force", action="store_true", help="Force a full rebuild, skipping pre-built asset download.")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, force=args.force)

    def __init__(self, bench: "Bench", force: bool = False) -> None:
        self.bench = bench
        self.force = force

    def run(self) -> None:
        from bench_cli.managers.process_manager import ProcessManagerFactory
        from bench_cli.managers.python_env_manager import PythonEnvManager

        manager = PythonEnvManager(self.bench)
        if self.force:
            manager.build_assets()
        else:
            for app in self.bench.apps():
                manager.build_assets_for_app(app)
        ProcessManagerFactory.create(self.bench).reload_workers(web_only=True)
