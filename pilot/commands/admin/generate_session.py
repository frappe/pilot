from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command
from pilot.exceptions import BenchError


@dataclass(kw_only=True)
class GenerateSessionCommand(Command):
    name: ClassVar[str] = "generate-admin-session"
    help: ClassVar[str] = "Issue a 5-minute one-time sign-in token (use --full-path for a sign-in URL)."

    full_path: Annotated[bool, Arg(help="Print the full admin URL with ?sid= instead of the bare token.")] = (
        False
    )

    def run(self) -> None:
        from admin.backend.auth import issue_login_token
        from pilot.utils import admin_url

        if not self.bench.config.admin.password:
            raise BenchError("Admin has no password set; configure [admin].password in bench.toml first.")
        token = issue_login_token(self._jwt_secret())
        if self.full_path:
            self.report(f"{admin_url(self.bench.config)}/?sid={urllib.parse.quote(token, safe='')}")
        else:
            self.report(token)

    def _jwt_secret(self) -> str:
        from admin.backend.auth import ensure_jwt_secret

        secret = ensure_jwt_secret(self.bench.path / "bench.toml")
        self.bench.config.admin.jwt_secret = secret
        return secret


def _default_ttl() -> int:
    # Discovery imports every command module, so this stays lazy - pilot.core
    # must not load just to build --help.
    from admin.backend.auth import DEFAULT_TTL

    return DEFAULT_TTL


@dataclass(kw_only=True)
class IssueSiteTokenCommand(Command):
    name: ClassVar[str] = "issue-site-token"
    help: ClassVar[str] = "Issue a scoped JWT for site-to-bench API calls."

    site: Annotated[str, Arg(help="Site name to scope the token to.")]
    ttl: Annotated[int, Arg(help="Token TTL in seconds (default: 86400).")] = field(
        default_factory=_default_ttl
    )

    def run(self) -> None:
        from admin.backend.auth import ensure_jwt_secret, issue_site_token

        secret = ensure_jwt_secret(self.bench.path / "bench.toml")
        self.bench.config.admin.jwt_secret = secret
        self.report(issue_site_token(secret, self.site, ttl=self.ttl))
