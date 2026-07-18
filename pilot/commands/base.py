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

    def confirm(
        self, prompt: str, *, skip: bool = False, error: type[Exception] = BenchError
    ) -> None:
        if skip:
            return
        try:
            answer = input(f"{prompt} [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            raise error("Aborted.")
