from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class EnrollCommand(Command):
    """Enroll this bench with Central."""

    name: ClassVar[str] = "enroll"
    group: ClassVar[str] = "admin"
    help: ClassVar[str] = "Exchange the bootstrap token for this bench's Central credential."

    endpoint: Annotated[str, Arg(help="Central API base URL.")] = ""
    bootstrap_token: Annotated[str, Arg(help="Single-use enrollment token.")] = ""
    seed_file: Annotated[
        str,
        Arg(help="JSON seed with central_endpoint and bootstrap_token."),
    ] = ""

    def run(self) -> None:
        from pilot.integrations.central import (
            default_seed_path,
            enroll_if_needed,
            seed,
            seed_from_metadata,
        )

        if self.endpoint and self.bootstrap_token:
            seed(self.bench, self.endpoint, self.bootstrap_token)
        elif self.seed_file:
            seed_from_metadata(self.bench, self.seed_file)
        elif not self.bench.config.central.auth_token and not self.bench.config.central.bootstrap_token:
            seed_from_metadata(self.bench, default_seed_path())
        if enroll_if_needed(self.bench):
            print("Enrolled with Central; credential + JWKS config written to bench.toml.")
        else:
            print("Already enrolled; nothing to do.")
