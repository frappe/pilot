from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class SetAdminPasswordCommand(Command):
    name: ClassVar[str] = "set-admin-password"
    help: ClassVar[str] = "Set the admin panel password (prompts if --password is omitted)."

    password: Annotated[str | None, Arg(help="New password; omit to be prompted securely.")] = None

    def run(self) -> None:
        import secrets

        from pilot.config import BenchConfig

        password = self.resolve_password(self.password)
        is_generated = not password
        if is_generated:
            password = secrets.token_urlsafe(16)

        with BenchConfig.open(self.bench.path) as config:
            config.admin.set_password(password)
        self.report("Admin password updated.")
        if is_generated:
            self.report(f"Generated password (shown once): {password}")
