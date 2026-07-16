from dataclasses import dataclass
from pathlib import Path


@dataclass
class MonitorConfig:
    system_log_path: Path = Path("/var/log/bench-system-stats.log")
    system_log_max_size: str = "500M"
    application_log_max_size: str = "500M"
    log_path: Path | None = None  # set by `bench setup production`

    @staticmethod
    def default_log_path(bench_name: str) -> Path:
        return Path(f"/var/log/{bench_name}-stats.log")
