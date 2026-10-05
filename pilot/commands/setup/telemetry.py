from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class SetupTelemetryCommand(Command):
    """Write this bench's Datum credential and install the log shipper."""

    name: ClassVar[str] = "telemetry"
    help: ClassVar[str] = "Write the metrics credential to common_config.toml and install Fluent Bit."
    group: ClassVar[str] = "setup"

    endpoint: Annotated[
        str | None,
        Arg(
            help="Telemetry base URL (e.g. https://datum.internal), written to common_config.toml. "
            "Fetched from Central when omitted"
        ),
    ] = None
    token: Annotated[
        str | None,
        Arg(
            help="Bearer JWT for this region's Datum, written to common_config.toml. Fetched from Central when omitted"
        ),
    ] = None

    def run(self) -> None:
        """Metrics and logs share one credential, so both are configured here.

        Only logs need installing: the monitor builds its shipper from this same config on
        every tick, so writing it is all metrics take."""
        from pilot.core.bench.telemetry import apply_credential
        from pilot.managers.fluentbit import LogsConfigurator

        apply_credential(
            self.bench, endpoint=self.endpoint, token=self.token, on_progress=self.report
        )
        telemetry = self.bench.config.telemetry

        if not (telemetry.endpoint and telemetry.token):
            self.report(
                "Telemetry is not configured. Set [telemetry] endpoint in common_config.toml,\n"
                "or pass --endpoint. Both are fetched from Central when it knows this region's Datum."
            )
            return

        from pilot.core.server.monitoring_datum import HAS_DATUM_CLIENT

        if not telemetry.is_shipping_metrics:
            self.report("Metrics are off.")
        elif not HAS_DATUM_CLIENT:
            self.report("Metrics stay in local logs: the Datum client (Pilot's 'metrics' extra) is not installed.")
        else:
            self.report(f"Metrics will ship to {telemetry.endpoint}.")
        if not telemetry.is_shipping_logs:
            self.report("Logs are off. Set [telemetry] logs_enabled to ship them.")
            return

        configurator = LogsConfigurator(self.bench)
        configurator.setup()
        configurator.install(telemetry)
        self.report("Fluent Bit installed. Logs will ship to " + telemetry.endpoint)
