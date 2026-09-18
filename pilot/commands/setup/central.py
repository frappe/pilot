from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class SetupCentralCommand(Command):
    """Hand this host to Central and alias the VM hostnames it hands out."""

    name: ClassVar[str] = "central"
    help: ClassVar[str] = "Enable Central management and alias its VM hostnames."
    group: ClassVar[str] = "setup"

    admin_pattern: Annotated[
        str,
        Arg(help="VM hostname glob to serve this bench's admin on, e.g. 'admin-vm-*.example.com'."),
    ] = ""
    site_pattern: Annotated[
        str,
        Arg(help="VM hostname glob to serve this bench's site on, e.g. 'site-*.example.com'."),
    ] = ""
    redirect: Annotated[
        bool,
        Arg(help="Redirect the VM hostname to the real domain instead of serving it."),
    ] = False
    rebootstrap: Annotated[
        bool,
        Arg(help="Apply this host's Central credential again, even if it already has one."),
    ] = False

    def run(self) -> None:
        self.bench.setup_central(
            admin_pattern=self.admin_pattern,
            site_pattern=self.site_pattern,
            redirect=self.redirect,
            rebootstrap=self.rebootstrap,
            on_progress=self.report,
        )
        self.report("Central is enabled for this host.")
