from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, ClassVar

from pilot.exceptions import BenchError

__all__ = ["Arg", "BenchMode", "Command"]

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class BenchMode(Enum):
    """How the registry resolves Command.bench before dispatch."""

    NONE = auto()
    OPTIONAL = auto()
    AUTO = auto()
    EXPLICIT = auto()


@dataclass(frozen=True)
class Arg:
    help: str = ""
    short: str | None = None
    cli: bool = True
    metavar: str | None = None
    required: bool = False


@dataclass
class Command:
    """Dataclass-backed CLI command base."""

    name: ClassVar[str]
    help: ClassVar[str] = ""
    group: ClassVar[str | None] = None
    bench_mode: ClassVar[BenchMode] = BenchMode.AUTO
    supports_all_benches: ClassVar[bool] = False

    bench: Bench | None = None

    def run(self) -> None:
        raise NotImplementedError

    def report(self, message: str) -> None:
        print(message)
        sys.stdout.flush()

    def resolve_password(self, value: str | None, label: str = "admin password") -> str:
        """A validated password from the flag or the terminal. Returns "" when neither
        supplied one, leaving the caller to generate it."""
        if value:
            return self.validated_password(value)
        return self.ask_password(label)

    def ask_password(self, label: str = "admin password") -> str:
        """Read a password from the terminal, validate it, then ask for confirmation.
        Returns "" with no TTY to ask on, so an unattended run can generate one."""
        import getpass

        from pilot.internal.validators import ADMIN_PASSWORD_REQUIREMENTS

        if not sys.stdin.isatty():
            return ""
        self.report(f"Password requirements: {ADMIN_PASSWORD_REQUIREMENTS}.")
        self.report("Leave blank to generate one.")
        password = getpass.getpass(f"New {label}: ")
        if not password:
            return ""
        self.validated_password(password)
        if password != getpass.getpass(f"Confirm {label}: "):
            raise BenchError("Passwords do not match.")
        return password

    def validated_password(self, password: str) -> str:
        """The password unchanged, or a BenchError naming the unmet requirements."""
        from pilot.internal.validators import ADMIN_PASSWORD_REQUIREMENTS, validate_admin_password

        if error := validate_admin_password(password):
            raise BenchError(f"{error} Password needs {ADMIN_PASSWORD_REQUIREMENTS}.")
        return password

    def confirm(self, prompt: str, *, skip: bool = False, error: type[Exception] = BenchError) -> None:
        if skip:
            return
        try:
            answer = input(f"{prompt} [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            raise error("Aborted.")
