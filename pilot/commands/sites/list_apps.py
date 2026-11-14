from __future__ import annotations

import sys
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
        """Print the site's apps."""
        bench_apps = {app.config.name for app in self.bench.apps()}
        for app in self.site.active_apps():
            self.report(app)
            # Stdout keeps one app name per line for scripts.
            if app not in bench_apps:
                print(
                    f"Warning: {app} is not on this bench. Uninstall it from the site to drop it.",
                    file=sys.stderr,
                )
