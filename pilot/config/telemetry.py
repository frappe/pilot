from dataclasses import dataclass


@dataclass
class TelemetryConfig:
    """Where this bench ships telemetry, and with what.

    `endpoint` is the base URL of the region's Datum, with no path: each shipper appends
    its own (`/v1/ingest` for metrics, `/v1/logs/ingest` for logs). One token, because
    Central mints a single credential per region and Datum tells the two apart by route.
    """

    endpoint: str = ""
    token: str = ""
    logs_enabled: bool = True
    metrics_enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "TelemetryConfig":
        """The telemetry config from its TOML table."""
        return cls(
            endpoint=data.get("endpoint", ""),
            token=data.get("token", ""),
            logs_enabled=data.get("logs_enabled", True),
            metrics_enabled=data.get("metrics_enabled", True),
        )

    @property
    def is_shipping_metrics(self) -> bool:
        """Whether metrics are shipped."""
        return self.metrics_enabled and bool(self.endpoint and self.token)

    @property
    def is_shipping_logs(self) -> bool:
        """Whether logs are shipped."""
        return self.logs_enabled and bool(self.endpoint and self.token)
