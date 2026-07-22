from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar

from pilot.commands import Arg, Command

if TYPE_CHECKING:
    from pilot.core.site import Site


@dataclass(kw_only=True)
class ListSiteAppsCommand(Command):
    name: ClassVar[str] = "list-site-apps"
    help: ClassVar[str] = "List apps installed on a site."

    site_name: Annotated[str, Arg(help="Site name (e.g. site1.localhost).", metavar="site")]

    def __post_init__(self) -> None:
        self.site: "Site" = self.bench.site(self.site_name)

    def run(self) -> None:
        for app in self.site.installed_apps():
            self.report(app)
