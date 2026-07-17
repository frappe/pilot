from __future__ import annotations

import argparse
import getpass
from typing import TYPE_CHECKING

from pilot.commands.base import Command
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class SetAdminPasswordCommand(Command):
    name = "set-admin-password"
    help = "Set the admin panel password (prompts if --password is omitted)."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--password", help="New password; omit to be prompted securely.")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, password=args.password)

    def __init__(self, bench: "Bench", password: str | None = None) -> None:
        self.bench = bench
        self.password = password

    def run(self) -> None:
        from pilot.config.toml_store import BenchTomlStore

        password = self.password or self._prompt()
        if not password:
            raise BenchError("Password must not be empty.")

        store = BenchTomlStore.for_bench(self.bench.path)
        with store.edit_raw() as data:
            data.setdefault("admin", {})["password"] = password
        self.bench.config.admin.password = password
        print("Admin password updated.")

    def _prompt(self) -> str:
        password = getpass.getpass("New admin password: ")
        if password != getpass.getpass("Confirm admin password: "):
            raise BenchError("Passwords do not match.")
        return password
