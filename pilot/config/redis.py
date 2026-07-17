import re
from dataclasses import dataclass
from typing import Optional

from pilot.exceptions import ConfigError

_VERSION_PATTERN = re.compile(r"^\d+(\.\d+)*$")
_PORT_MIN = 1024
_PORT_MAX = 65535


@dataclass
class RedisConfig:
    cache_port: int = 13000
    queue_port: int = 11000
    version: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "RedisConfig":
        return cls(
            cache_port=data.get("cache_port", 13000),
            queue_port=data.get("queue_port", 11000),
            version=data.get("version"),
        )

    def validate(self) -> None:
        for name, port in (("redis.cache_port", self.cache_port), ("redis.queue_port", self.queue_port)):
            if not (_PORT_MIN <= port <= _PORT_MAX):
                raise ConfigError(f"{name} {port} is out of range. Must be between {_PORT_MIN} and {_PORT_MAX}.")
        if self.cache_port == self.queue_port:
            raise ConfigError(f"redis.cache_port and redis.queue_port must be distinct, but both are set to {self.cache_port}.")
        if self.version and not _VERSION_PATTERN.match(self.version):
            raise ConfigError(f"redis.version '{self.version}' is invalid. Must be a version string like '7' or '7.0'.")
