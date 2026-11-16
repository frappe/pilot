from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class SetupTelemetryCommand(Command):
    """Write this bench's Datum credential."""

    name: ClassVar[str] = "telemetry"
    help: ClassVar[str] = "Write the Datum credential to common_config.toml."
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
        """The monitor reads this config on every tick, so writing it is all telemetry takes."""
        from pilot.core.bench.telemetry import apply_credential

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

        if telemetry.is_enabled:
            self.report(f"Telemetry will be sent to {telemetry.endpoint}.")
        else:
            self.report("Telemetry is off. Set [telemetry] enabled to send it.")
