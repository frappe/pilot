from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import Command


@dataclass(kw_only=True)
class RemoveLogsCommand(Command):
    """Stop and disable the Fluent Bit log shipper."""

    name: ClassVar[str] = "logs"
    help: ClassVar[str] = "Stop and disable the Fluent Bit log shipper."
    group: ClassVar[str] = "remove"

    def run(self) -> None:
        from pilot.managers.fluentbit import LogsConfigurator
        from pilot.managers.systemd_user import user_service_installed

        configurator = LogsConfigurator(self.bench)
        if not user_service_installed(configurator.unit_name):
            self.report("Fluent Bit is not installed.")
            return
        configurator.remove()
        self.report("Fluent Bit removed.")
