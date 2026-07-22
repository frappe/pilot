from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class BuildCommand(Command):
    name: ClassVar[str] = "build"
    help: ClassVar[str] = "Build assets (downloads pre-built if available)."

    force: Annotated[bool, Arg(help="Force a full rebuild, skipping pre-built asset download.")] = False

    def run(self) -> None:
        self.bench.rebuild_assets(force=self.force)
