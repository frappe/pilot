from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands.base import BenchMode, Command


@dataclass(kw_only=True)
class DropBenchCommand(Command):
    name: ClassVar[str] = "drop"
    help: ClassVar[str] = "Delete a bench (must have no sites), tearing down its production services and nginx."
    # Deleting whichever bench happens to be active by default would be too easy
    # to trigger by accident, so require an explicit -b/--bench (or running from
    # inside the bench dir).
    bench_mode: ClassVar[BenchMode] = BenchMode.EXPLICIT

    skip_confirm: bool = False

    def run(self) -> None:
        self.bench.ensure_no_sites()
        self.confirm(
            f"Permanently delete bench '{self.bench.config.name}' and its database?",
            skip=self.skip_confirm,
        )
        self.bench.drop(on_progress=self.print)
