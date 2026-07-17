from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar

from pilot.commands.base import Arg, Command

if TYPE_CHECKING:
    from pilot.core.app import App


@dataclass(kw_only=True)
class RemoveAppCommand(Command):
    name: ClassVar[str] = "remove-app"
    help: ClassVar[str] = "Remove an app from the bench."

    app_name: Annotated[str, Arg(help="App name to remove.", metavar="app")]
    skip_confirm: bool = False
    force: Annotated[bool, Arg(cli=False)] = False

    def __post_init__(self) -> None:
        self.app: "App" = self.bench.app(self.app_name)

    def run(self) -> None:
        self.app.ensure_removable()
        self.confirm(f"Remove '{self.app_name}' from all sites and the bench?", skip=self.skip_confirm)
        self.app.remove(force=self.force, on_progress=self.print)
