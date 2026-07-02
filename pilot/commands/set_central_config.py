from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from pilot.commands.base import Command
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class SetCentralConfigCommand(Command):
    """Persist the Central callback endpoint + pilot auth token that Atlas hands the
    bench at deploy, so pilot→Central calls can authenticate. Merges into
    bench.toml (bench-owned config) without disturbing the other sections."""

    name = "set-central-config"
    help = "Store the Central endpoint + pilot auth token in bench.toml."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--endpoint", required=True, help="Central API base URL the pilot calls back on")
        parser.add_argument("--token", required=True, help="Opaque token the pilot presents to Central")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, endpoint=args.endpoint, token=args.token)

    def __init__(self, bench: "Bench", endpoint: str, token: str) -> None:
        self.bench = bench
        self.endpoint = endpoint
        self.token = token

    def run(self) -> None:
        from pilot.config.toml_store import BenchTomlStore

        store = BenchTomlStore.for_bench(self.bench.path)
        try:
            config = store.read_raw()
        except FileNotFoundError as exc:
            raise BenchError(f"{store.path} not found — is this a bench?") from exc
        except ValueError as exc:
            raise BenchError(f"{store.path} contains invalid TOML: {exc}") from exc
        config.setdefault("central", {})["endpoint"] = self.endpoint
        config["central"]["auth_token"] = self.token
        store.write_raw(config)
        self.bench.config.central.endpoint = self.endpoint
        self.bench.config.central.auth_token = self.token
        print("Central config written to bench.toml")
