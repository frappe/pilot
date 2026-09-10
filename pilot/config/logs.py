from dataclasses import dataclass


@dataclass
class LogsConfig:
    """Where Fluent Bit ships logs. Shipping is off until all three are set."""

    endpoint: str = ""
    token: str = ""
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "LogsConfig":
        return cls(
            endpoint=data.get("endpoint", ""),
            token=data.get("token", ""),
            enabled=data.get("enabled", True),
        )

    @property
    def is_enabled(self) -> bool:
        return self.enabled and bool(self.endpoint and self.token)
