from dataclasses import dataclass
from pathlib import Path


@dataclass
class MonitorConfig:
    system_log_path: Path = Path("/var/log/bench-system-stats.log")
    authority_file_path: Path = Path("/var/log/.bench-authority")
    system_log_max_size: str = "500M"
    application_log_max_size: str = "500M"
    log_path: Path | None = None  # set by `bench setup production`

    @classmethod
    def from_dict(cls, data: dict) -> "MonitorConfig":
        return cls(
            system_log_path=Path(data.get("system_log_path", "/var/log/bench-system-stats.log")),
            authority_file_path=Path(data.get("authority_file_path", "/var/log/.bench-authority")),
            system_log_max_size=data.get("system_log_max_size", "500M"),
            application_log_max_size=data.get("application_log_max_size", "500M"),
            log_path=Path(data["log_path"]) if "log_path" in data else None,
        )

    @staticmethod
    def default_log_path(bench_name: str) -> Path:
        return Path(f"/var/log/{bench_name}-stats.log")
