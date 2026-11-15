from dataclasses import dataclass


@dataclass
class TelemetryConfig:
    """Where this bench sends telemetry, and with what.

    `endpoint` is the base URL of the region's Datum, with no path. Central mints the token,
    and Datum takes it for both metrics and update events.
    """

    endpoint: str = ""
    token: str = ""
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "TelemetryConfig":
        """The telemetry config from its TOML table."""
        return cls(
            endpoint=data.get("endpoint", ""),
            token=data.get("token", ""),
            enabled=data.get("enabled", True),
        )

    @property
    def is_enabled(self) -> bool:
        """Whether telemetry is sent."""
        return self.enabled and bool(self.endpoint and self.token)
