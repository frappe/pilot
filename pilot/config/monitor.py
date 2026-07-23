from dataclasses import dataclass, field
from pathlib import Path


def _log_dir() -> Path:
    """Monitor logs live beside the install, not in root-owned /var/log."""
    from pilot.utils import cli_root

    return cli_root() / "logs"


@dataclass
class MonitorConfig:
    system_log_path: Path = field(default_factory=lambda: _log_dir() / "bench-system-stats.log")
    db_log_path: Path = field(default_factory=lambda: _log_dir() / "bench-db-stats.log")
    slow_query_log_path: Path = field(default_factory=lambda: _log_dir() / "bench-slow-queries.json")
    authority_file_path: Path = field(default_factory=lambda: _log_dir() / ".bench-authority")
    system_log_max_size: str = "500M"
    application_log_max_size: str = "500M"
    log_path: Path | None = None  # set by `bench setup production`

    @classmethod
    def from_dict(cls, data: dict) -> "MonitorConfig":
        log_dir = _log_dir()
        return cls(
            system_log_path=Path(data.get("system_log_path", log_dir / "bench-system-stats.log")),
            db_log_path=Path(data.get("db_log_path", log_dir / "bench-db-stats.log")),
            slow_query_log_path=Path(data.get("slow_query_log_path", log_dir / "bench-slow-queries.json")),
            authority_file_path=Path(data.get("authority_file_path", log_dir / ".bench-authority")),
            system_log_max_size=data.get("system_log_max_size", "500M"),
            application_log_max_size=data.get("application_log_max_size", "500M"),
            log_path=Path(data["log_path"]) if "log_path" in data else None,
        )

    @staticmethod
    def default_log_path(bench_name: str) -> Path:
        return _log_dir() / f"{bench_name}-stats.log"
