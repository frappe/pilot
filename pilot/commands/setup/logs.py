from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class SetupLogsCommand(Command):
    """Install Fluent Bit and write its config from common_config.toml."""

    name: ClassVar[str] = "logs"
    help: ClassVar[str] = "Install Fluent Bit and write its config from common_config.toml."
    group: ClassVar[str] = "setup"

    endpoint: Annotated[
        str | None,
        Arg(help="Datum logs API endpoint (e.g. https://datum.internal), written to common_config.toml. Fetched from Central when omitted"),
    ] = None
    token: Annotated[
        str | None,
        Arg(help="Bearer JWT for the logs path, written to common_config.toml. Fetched from Central when omitted"),
    ] = None

    def run(self) -> None:
        from pilot.config import BenchConfig
        from pilot.managers.fluentbit import LogsConfigurator

        endpoint, token = self.endpoint, self.token
        if not (endpoint and token):
            fetched_endpoint, fetched_token = self._fetch_log_credentials()
            endpoint = endpoint or fetched_endpoint
            token = token or fetched_token

        if endpoint or token:
            with BenchConfig.open(self.bench.path) as config:
                if endpoint:
                    config.logs.endpoint = endpoint
                if token:
                    config.logs.token = token

        if endpoint:
            self.bench.config.logs.endpoint = endpoint
        if token:
            self.bench.config.logs.token = token

        log_config = self.bench.config.logs
        if not log_config.is_enabled:
            self.report(
                "Log shipping is not enabled. Set [logs] endpoint in common_config.toml,\n"
                "or pass --endpoint. Both are fetched from Central when it knows this region's Datum."
            )
            return

        configurator = LogsConfigurator(self.bench)
        configurator.setup()
        configurator.install(log_config)
        self.report("Fluent Bit installed. Logs will ship to " + log_config.endpoint)

    def _fetch_log_credentials(self) -> tuple[str | None, str | None]:
        """Central mints the JWT and names the region's Datum to present it to."""
        from pilot.integrations.central import CentralClient

        if not self.bench.config.central.enabled:
            return None, None
        credentials = CentralClient().log_token()
        return credentials.get("endpoint") or None, credentials.get("token") or None
