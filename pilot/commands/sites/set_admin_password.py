from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command
from pilot.exceptions import BenchError


@dataclass(kw_only=True)
class SetAdminPasswordCommand(Command):
    """Update the admin panel password.

    Prompts on an interactive stdin. Auto-generation requires a TTY stdout to
    avoid printing credentials into redirected output; use ``--password`` instead.
    """

    name: ClassVar[str] = "set-admin-password"
    help: ClassVar[str] = "Set the admin panel password (prompts interactively; use --password when stdout is redirected)."

    password: Annotated[str | None, Arg(help="New password; omit to be prompted or auto-generated on a TTY stdout.")] = None

    def run(self) -> None:
        import secrets

        from pilot.config import BenchConfig

        password = self.resolve_password(self.password)

        if not password:
            if not sys.stdout.isatty():
                raise BenchError(
                    "Cannot safely generate a password in a non-interactive environment. "
                    "Use --password to supply one explicitly."
                )
            password = secrets.token_urlsafe(12)
            self.report(f"Generated admin password: {password}")

        with BenchConfig.open(self.bench.path) as config:
            config.admin.set_password(password)
        self.report("Admin password updated.")
