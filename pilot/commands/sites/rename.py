from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class RenameSiteCommand(Command):
    name: ClassVar[str] = "rename-site"
    help: ClassVar[str] = "Rename a site in this bench."

    old_name: Annotated[str, Arg(help="Current site name.")]
    new_name: Annotated[str, Arg(help="New site name (hostname).")]

    def run(self) -> None:
        self.bench.site(self.old_name).rename_to(self.new_name, on_progress=self.report)
