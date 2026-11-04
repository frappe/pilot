from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal

from pilot.commands import Arg, BenchMode, Command


@dataclass(kw_only=True)
class AutoUpdateCommand(Command):
    name: ClassVar[str] = "auto-update"
    group: ClassVar[str] = "admin"
    help: ClassVar[str] = "Turn daily automatic Pilot updates on or off for this host, or show the setting."
    bench_mode: ClassVar[BenchMode] = BenchMode.NONE

    state: Annotated[Literal["on", "off", "status"], Arg(help="on, off, or status.")]

    def run(self) -> None:
        from pilot.core.server import Server

        auto_update = Server().auto_update
        if self.state == "on":
            auto_update.enable()
        elif self.state == "off":
            auto_update.disable()
        self.report(f"Automatic Pilot updates are {'on' if auto_update.is_enabled else 'off'}.")
