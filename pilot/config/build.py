from dataclasses import dataclass

from pilot.exceptions import ConfigError


@dataclass
class BuildConfig:
    memory_limit_mb: int = 0  # 0 = auto (85% of free memory at build time)
    node_heap_limit_mb: int = 0  # 0 = auto
    node_heap_min_mb: int = 2048
    node_heap_max_mb: int = 6144
    node_heap_available_percent: int = 60

    @classmethod
    def from_dict(cls, data: dict) -> "BuildConfig":
        d = cls()
        return cls(
            memory_limit_mb=data.get("memory_limit_mb", d.memory_limit_mb),
            node_heap_limit_mb=data.get("node_heap_limit_mb", d.node_heap_limit_mb),
            node_heap_min_mb=data.get("node_heap_min_mb", d.node_heap_min_mb),
            node_heap_max_mb=data.get("node_heap_max_mb", d.node_heap_max_mb),
            node_heap_available_percent=data.get(
                "node_heap_available_percent", d.node_heap_available_percent
            ),
        )

    def validate(self) -> None:
        non_negative = {
            "memory_limit_mb": self.memory_limit_mb,
            "node_heap_limit_mb": self.node_heap_limit_mb,
        }
        for name, value in non_negative.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ConfigError(f"build.{name} must be a non-negative integer, got '{value}'.")

        positive = {
            "node_heap_min_mb": self.node_heap_min_mb,
            "node_heap_max_mb": self.node_heap_max_mb,
        }
        for name, value in positive.items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ConfigError(f"build.{name} must be a positive integer, got '{value}'.")

        if self.node_heap_min_mb > self.node_heap_max_mb:
            raise ConfigError("build.node_heap_min_mb must not exceed build.node_heap_max_mb.")

        if (
            isinstance(self.node_heap_available_percent, bool)
            or not isinstance(self.node_heap_available_percent, int)
            or not 1 <= self.node_heap_available_percent <= 100
        ):
            raise ConfigError(
                "build.node_heap_available_percent must be an integer between 1 and 100."
            )
