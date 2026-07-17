from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands.base import Arg, Command
from pilot.exceptions import BenchError


@dataclass(kw_only=True)
class SetCentralConfigCommand(Command):
    """Persist the Central callback endpoint + pilot auth token that Atlas hands the
    bench at deploy, so pilot→Central calls can authenticate. Merges into
    bench.toml (bench-owned config) without disturbing the other sections."""

    name: ClassVar[str] = "set-central-config"
    help: ClassVar[str] = "Store the Central endpoint + pilot auth token in bench.toml."

    endpoint: Annotated[str, Arg(help="Central API base URL the pilot calls back on", required=True)]
    token: Annotated[str, Arg(help="Opaque token the pilot presents to Central", required=True)]

    def run(self) -> None:
        from pilot.config.toml_store import BenchTomlStore

        store = BenchTomlStore.for_bench(self.bench.path)
        try:
            with store.edit_raw() as config:
                config.setdefault("central", {})["endpoint"] = self.endpoint
                config["central"]["auth_token"] = self.token
        except FileNotFoundError as exc:
            raise BenchError(f"{store.path} not found — is this a bench?") from exc
        except ValueError as exc:
            raise BenchError(f"{store.path} contains invalid TOML: {exc}") from exc
        self.bench.config.central.endpoint = self.endpoint
        self.bench.config.central.auth_token = self.token
        self.print("Central config written to bench.toml")
