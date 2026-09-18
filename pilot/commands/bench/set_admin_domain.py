from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class SetAdminDomainCommand(Command):
    name: ClassVar[str] = "set-admin-domain"
    help: ClassVar[str] = "Point the admin at a different hostname."

    domain: Annotated[str, Arg(help="New admin hostname.")]
    tls: Annotated[bool | None, Arg(help="Serve the admin over HTTPS.")] = None

    def run(self) -> None:
        self.bench.change_admin_domain(self.domain, self.tls, on_progress=self.report)
