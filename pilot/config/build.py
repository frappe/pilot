from dataclasses import dataclass

from pilot.exceptions import ConfigError


@dataclass
class BuildConfig:
    memory_limit_mb: int = 0  # 0 = auto (85% of free memory at build time)

    @classmethod
    def from_dict(cls, data: dict) -> "BuildConfig":
        d = cls()
        return cls(memory_limit_mb=data.get("memory_limit_mb", d.memory_limit_mb))

    def validate(self) -> None:
        if isinstance(self.memory_limit_mb, bool) or not isinstance(self.memory_limit_mb, int) or self.memory_limit_mb < 0:
            raise ConfigError(
                f"build.memory_limit_mb must be a non-negative integer, got '{self.memory_limit_mb}'."
            )
