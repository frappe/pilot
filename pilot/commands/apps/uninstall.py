from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar

from pilot.commands.base import Arg, Command

if TYPE_CHECKING:
    from pilot.core.site import Site


@dataclass(kw_only=True)
class UninstallAppCommand(Command):
    name: ClassVar[str] = "uninstall-app"
    help: ClassVar[str] = (
        "Uninstall one or more apps from a site, also remove app from bench if not installed on any site"
    )

    site_name: Annotated[str, Arg(help="Site name.", metavar="site")]
    app_names: Annotated[list[str], Arg(help="App name(s) to uninstall.", metavar="apps")]
    force: Annotated[bool, Arg(help="Uninstall even if not tracked as installed.")] = False

    def __post_init__(self) -> None:
        from pilot.core.site import Site

        self.site: "Site" = Site.for_name(self.site_name, self.bench)

    def run(self) -> None:
        self.site.uninstall_apps(self.app_names, force=self.force, on_progress=self.print)
