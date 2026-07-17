from __future__ import annotations

import getpass
from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands.base import Arg, Command
from pilot.exceptions import BenchError


@dataclass(kw_only=True)
class SetAdminPasswordCommand(Command):
    name: ClassVar[str] = "set-admin-password"
    help: ClassVar[str] = "Set the admin panel password (prompts if --password is omitted)."

    password: Annotated[str | None, Arg(help="New password; omit to be prompted securely.")] = None

    def run(self) -> None:
        from pilot.config.toml_store import BenchTomlStore

        password = self.password or self._prompt()
        if not password:
            raise BenchError("Password must not be empty.")

        store = BenchTomlStore.for_bench(self.bench.path)
        with store.edit_raw() as data:
            data.setdefault("admin", {})["password"] = password
        self.bench.config.admin.password = password
        self.print("Admin password updated.")

    def _prompt(self) -> str:
        password = getpass.getpass("New admin password: ")
        if password != getpass.getpass("Confirm admin password: "):
            raise BenchError("Passwords do not match.")
        return password
