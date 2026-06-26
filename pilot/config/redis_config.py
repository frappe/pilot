from dataclasses import dataclass
from typing import Optional


@dataclass
class RedisConfig:
    cache_port: int = 13000
    queue_port: int = 11000
    version: Optional[str] = None
