from pathlib import Path

from pilot.internal.jsonl_log import JsonlLog


def monitor_log_dir() -> Path:
    """Monitor logs live beside the install, not in root-owned /var/log."""
    from pilot.core.server import Server

    return Server().root / "system" / "logs"


def system_log_path() -> Path:
    """The system metrics log."""
    return monitor_log_dir() / "system-stats.log"


def db_log_path() -> Path:
    """The database metrics log."""
    return monitor_log_dir() / "db-stats.log"


def slow_query_log_path() -> Path:
    """The slow query log."""
    return monitor_log_dir() / "slow-queries.json"


def bench_log_path(bench_name: str) -> Path:
    """The bench metrics log."""
    return monitor_log_dir() / f"{bench_name}-stats.log"


def get_central_request_log() -> JsonlLog:
    """One record for each call to Central: the method and its result."""
    return JsonlLog(monitor_log_dir(), "central-requests")


def get_update_log() -> JsonlLog:
    """One record for each app of an update that failed or ended."""
    return JsonlLog(monitor_log_dir(), "updates")
