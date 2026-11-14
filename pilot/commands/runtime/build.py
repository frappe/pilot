from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class BuildCommand(Command):
    name: ClassVar[str] = "build"
    help: ClassVar[str] = "Build assets (downloads pre-built if available)."

    apps: Annotated[list[str], Arg(help="Apps to build. Builds every app when omitted.")] = field(
        default_factory=list
    )
    force: Annotated[bool, Arg(help="Force a full rebuild, skipping pre-built asset download.")] = False

    def run(self) -> None:
        """Build the assets."""
        self.bench.rebuild_assets(force=self.force, apps=self.apps)
