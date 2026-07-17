from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar

from pilot.commands.base import Arg, Command
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.site import Site


@dataclass(kw_only=True)
class InstallAppCommand(Command):
    name: ClassVar[str] = "install-app"
    help: ClassVar[str] = "Install one or more apps on a site."

    site_name: Annotated[str, Arg(help="Site name.", metavar="site")]
    app_names: Annotated[list[str], Arg(help="App name(s) to install.", metavar="apps")]
    force: Annotated[bool, Arg(help="Reinstall even if already present.")] = False

    def __post_init__(self) -> None:
        from pilot.core.site import Site

        self.site: "Site" = Site.for_name(self.site_name, self.bench)

    def run(self) -> None:
        if not self.site.exists:
            raise BenchError(f"Site '{self.site_name}' does not exist.")

        installed = self.site.list_apps()
        for app_name in self.app_names:
            app = self.bench.app(app_name)
            if not self.force and app.config.name in installed:
                self.print(f"'{app_name}' is already installed on '{self.site_name}', skipping.")
                continue
            self.print(f"Installing '{app_name}' on '{self.site_name}'...")
            self.site.install_app(app)
            self.print(f"'{app_name}' installed on '{self.site_name}'.")
