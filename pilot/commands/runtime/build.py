from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands.base import Arg, Command


@dataclass(kw_only=True)
class BuildCommand(Command):
    name: ClassVar[str] = "build"
    help: ClassVar[str] = "Build assets (downloads pre-built if available)."

    force: Annotated[bool, Arg(help="Force a full rebuild, skipping pre-built asset download.")] = False

    def run(self) -> None:
        from pilot.managers.processes.local import ProcessManager
        from pilot.managers.python_environment import PythonEnvManager

        manager = PythonEnvManager(self.bench)
        if self.force:
            manager.build_assets()
        else:
            for app in self.bench.apps():
                manager.build_assets_for_app(app)
        ProcessManager.for_bench(self.bench).reload_workers(web_only=True)
