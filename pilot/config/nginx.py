from dataclasses import dataclass
from pathlib import Path

from pilot.exceptions import ConfigError


@dataclass
class NginxConfig:
    """Host nginx settings. Only the listen ports are configurable, in common_config.toml."""

    http_port: int = 80
    https_port: int = 443
    # Empty means "use the platform default" - resolved by whoever consumes
    # this (NginxManager), which already knows how to find it per-OS.
    config_dir: Path = Path("")
    worker_processes: str = "auto"
    client_max_body_size: str = "50m"

    @classmethod
    def from_dict(cls, data: dict) -> "NginxConfig":
        config = cls(http_port=data.get("http_port", 80), https_port=data.get("https_port", 443))
        config.validate()
        return config

    @property
    def ports(self) -> dict[str, int]:
        return {"http_port": self.http_port, "https_port": self.https_port}

    def validate(self) -> None:
        for name, port in self.ports.items():
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise ConfigError(f"nginx.{name} must be a port number between 1 and 65535, got {port!r}.")
        if self.http_port == self.https_port:
            raise ConfigError("nginx.http_port and nginx.https_port must differ.")
