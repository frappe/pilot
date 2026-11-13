import re
from dataclasses import dataclass

from pilot.exceptions import ConfigError


@dataclass
class RedisConfig:
    port_min = 1024
    port_max = 65535

    cache_port: int = 13000
    queue_port: int = 11000
    version: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "RedisConfig":
        """The Redis config from its TOML table."""
        return cls(
            cache_port=data.get("cache_port", 13000),
            queue_port=data.get("queue_port", 11000),
            version=data.get("version"),
        )

    def validate(self) -> None:
        """Raise when a port is invalid."""
        for name, port in (
            ("redis.cache_port", self.cache_port),
            ("redis.queue_port", self.queue_port),
        ):
            if not (self.port_min <= port <= self.port_max):
                raise ConfigError(
                    f"{name} {port} is out of range. Must be between {self.port_min} and {self.port_max}."
                )
        if self.cache_port == self.queue_port:
            raise ConfigError(
                f"redis.cache_port and redis.queue_port must be distinct, but both are set to {self.cache_port}."
            )
        if self.version and not re.match(r"^\d+(\.\d+)*$", self.version):
            raise ConfigError(
                f"redis.version '{self.version}' is invalid. Must be a version string like '7' or '7.0'."
            )
