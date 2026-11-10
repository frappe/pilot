from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


def _default_ttl() -> int:
    # The parser build never runs this - the dataclass runs it when the command
    # is actually built - so importing the session module here is free.
    from admin.backend.internal.session import Session

    return Session.DEFAULT_TTL


@dataclass(kw_only=True)
class IssueSiteTokenCommand(Command):
    name: ClassVar[str] = "issue-site-token"
    group: ClassVar[str] = "admin"
    help: ClassVar[str] = "Issue a scoped JWT for site-to-bench API calls."

    site: Annotated[str, Arg(help="Site name to scope the token to.")]
    ttl: Annotated[int, Arg(help="Token TTL in seconds (default: 86400).")] = field(
        default_factory=_default_ttl
    )

    def run(self) -> None:
        from admin.backend.internal.session import Session

        self.report(Session(self.bench).issue_site_token(self.site, ttl=self.ttl))
