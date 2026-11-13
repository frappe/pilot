from dataclasses import dataclass

from pilot.exceptions import ConfigError


@dataclass
class LiteModeConfig:
    """One process serving web, realtime and background jobs, recycled once a
    limit is reached and traffic has gone quiet."""

    enabled: bool = False
    restart_after_requests: int = 5000  # 0 = no limit
    restart_after_jobs: int = 500  # 0 = no limit
    restart_idle_seconds: int = 300
    request_drain_seconds: int = 60
    job_drain_seconds: int = 600

    @classmethod
    def from_dict(cls, data: dict) -> "LiteModeConfig":
        """The lite mode config from its TOML table."""
        d = cls()
        return cls(
            enabled=bool(data.get("enabled", d.enabled)),
            restart_after_requests=data.get("restart_after_requests", d.restart_after_requests),
            restart_after_jobs=data.get("restart_after_jobs", d.restart_after_jobs),
            restart_idle_seconds=data.get("restart_idle_seconds", d.restart_idle_seconds),
            request_drain_seconds=data.get("request_drain_seconds", d.request_drain_seconds),
            job_drain_seconds=data.get("job_drain_seconds", d.job_drain_seconds),
        )

    @property
    def stop_timeout(self) -> int:
        """Graceful-stop budget: web drain, then the job in flight, plus re-exec slack."""
        return self.request_drain_seconds + self.job_drain_seconds + 30

    def validate(self) -> None:
        """Raise when a restart count is invalid."""
        for name in ("restart_after_requests", "restart_after_jobs"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise ConfigError(f"lite.{name} must be a non-negative integer, got '{value}'.")
        for name in ("restart_idle_seconds", "request_drain_seconds", "job_drain_seconds"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 1:
                raise ConfigError(f"lite.{name} must be a positive integer, got '{value}'.")
