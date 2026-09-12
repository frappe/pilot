from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from pilot.commands import Command

if TYPE_CHECKING:
    from pilot.core.site import Site


@dataclass(kw_only=True)
class ListSiteAppsCommand(Command):
    name: ClassVar[str] = "list-site-apps"
    help: ClassVar[str] = "List apps installed on a site."

    site_name: str

    def __post_init__(self) -> None:
        self.site: "Site" = self.bench.site(self.site_name)

    def run(self) -> None:
        for app in self.site.active_apps():
            self.report(app)
