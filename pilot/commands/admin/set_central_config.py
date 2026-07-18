from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command
from pilot.exceptions import BenchError


@dataclass(kw_only=True)
class SetCentralConfigCommand(Command):
    """Persist the Central endpoint and pilot auth token in bench.toml."""

    name: ClassVar[str] = "set-central-config"
    help: ClassVar[str] = "Store the Central endpoint + pilot auth token in bench.toml."

    endpoint: Annotated[str, Arg(help="Central API base URL the pilot calls back on", required=True)]
    token: Annotated[str, Arg(help="Opaque token the pilot presents to Central", required=True)]

    def run(self) -> None:
        from pilot.config import BenchConfig

        toml_path = BenchConfig.toml_path(self.bench.path)
        try:
            with BenchConfig.open(self.bench.path, mode="raw") as config:
                config.setdefault("central", {})["endpoint"] = self.endpoint
                config["central"]["auth_token"] = self.token
        except FileNotFoundError as exc:
            raise BenchError(f"{toml_path} not found - is this a bench?") from exc
        except ValueError as exc:
            raise BenchError(f"{toml_path} contains invalid TOML: {exc}") from exc
        self.bench.config.central.endpoint = self.endpoint
        self.bench.config.central.auth_token = self.token
        self.report("Central config written to bench.toml")
