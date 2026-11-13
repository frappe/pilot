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
        """Stop shipping logs."""
        from pilot.config import BenchConfig
        from pilot.managers.fluentbit import LogsConfigurator
        from pilot.managers.systemd_user import user_service_installed

        configurator = LogsConfigurator(self.bench)
        if not user_service_installed(configurator.unit_name):
            self.report("Fluent Bit is not installed.")
            return
        configurator.remove()
        # The monitor installs it again while logs are on.
        with BenchConfig.open(self.bench.path) as config:
            config.telemetry.logs_enabled = False
        self.report("Fluent Bit removed. Set [telemetry] logs_enabled to ship logs again.")
