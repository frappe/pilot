from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar

from pilot.commands.base import Arg, Command
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.site import Site


@dataclass(kw_only=True)
class NewSiteCommand(Command):
    name: ClassVar[str] = "new-site"
    help: ClassVar[str] = "Create a new site and add it to bench.toml."

    site_name: Annotated[str, Arg(help="Site name (e.g. site2.localhost).", metavar="name")]
    admin_password: Annotated[str, Arg(help="Frappe admin password.")] = "admin"
    apps: Annotated[list[str] | None, Arg(help="Apps to assign (defaults to framework app).")] = None
    db_type: Annotated[str | None, Arg(cli=False)] = None

    def __post_init__(self) -> None:
        if not isinstance(self.admin_password, str) or not self.admin_password.strip():
            raise BenchError("Site Administrator password must not be empty.")
        if not self.apps:
            framework = self.bench.config.framework_app.name
            self.apps = [framework] if framework else []
        self.site: "Site | None" = None

    def run(self) -> None:
        from pilot.core.site import Site

        self.site = Site.provision(
            self.bench, self.site_name, self.apps, self.admin_password,
            db_type=self.db_type, on_progress=self.print,
        )
