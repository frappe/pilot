from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from pilot.commands.base import Command

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class EnrollCommand(Command):
    """Exchange the seeded bootstrap token for this bench's Central credential + JWKS config,
    written to bench.toml. Idempotent, so it's safe to run on every boot."""

    name = "enroll"
    help = "Exchange the bootstrap token for this bench's Central credential + JWKS config."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--endpoint", default="", help="Central API base URL (seeded if given).")
        parser.add_argument("--bootstrap-token", default="", help="Single-use enrollment token (seeded if given).")
        parser.add_argument("--seed-file", default="",
                            help="Path to a JSON seed {central_endpoint, bootstrap_token} dropped by VM "
                                 "create-time metadata (boot-free enrollment).")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, endpoint=args.endpoint, bootstrap_token=args.bootstrap_token, seed_file=args.seed_file)

    def __init__(self, bench: "Bench", endpoint: str = "", bootstrap_token: str = "", seed_file: str = "") -> None:
        self.bench = bench
        self.endpoint = endpoint
        self.bootstrap_token = bootstrap_token
        self.seed_file = seed_file

    def run(self) -> None:
        from pilot.core.central_bootstrap import default_seed_path, enroll_if_needed, seed, seed_from_metadata

        # Seed order: explicit args → --seed-file → the canonical metadata path, so a bare
        # `bench enroll` on first boot picks up whatever VM metadata dropped there.
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
