from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal

from pilot.commands.base import Arg, Command


@dataclass(kw_only=True)
class SetupProductionCommand(Command):
    name: ClassVar[str] = "production"
    help: ClassVar[str] = "Deploy a bench to production (process manager + nginx)."
    group: ClassVar[str] = "setup"

    process_manager: Annotated[
        Literal["systemd", "supervisord"] | None,
        Arg(help="Process manager to deploy with (defaults to production.process_manager in bench.toml, or systemd)."),
    ] = None
    admin_domain: Annotated[
        str | None,
        Arg(help="Admin domain the deployment is reached at (required: pass it here or set admin.domain in bench.toml)."),
    ] = None
    # None = leave the bench.toml value untouched; only --tls turns it on.
    tls: Annotated[
        bool | None,
        Arg(
            help="Terminate TLS via Let's Encrypt for the admin and SSL-enabled sites. "
            "Omit to serve plain HTTP (a central proxy may terminate TLS upstream)."
        ),
    ] = None
    letsencrypt_email: Annotated[
        str | None,
        Arg(help="Contact email for Let's Encrypt (required with --tls unless letsencrypt.email is already set in bench.toml)."),
    ] = None
    best_effort_tls: Annotated[bool, Arg(cli=False)] = False

    def run(self) -> None:
        self.bench.setup_production(
            process_manager=self.process_manager,
            admin_domain=self.admin_domain,
            admin_tls=self.tls,
            letsencrypt_email=self.letsencrypt_email,
            best_effort_tls=self.best_effort_tls,
            on_progress=self.print,
        )
