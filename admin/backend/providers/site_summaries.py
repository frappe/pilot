from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from admin.backend.providers.site_monitoring import SiteMonitoringProvider
from admin.backend.providers.windowed_log import WindowedLogProvider

REQUEST_BUCKETS = 24


class SiteSummaryProvider:
    """Uptime and request summaries for every site, reading each shared bench log once."""

    def __init__(self, bench_root: Path) -> None:
        self.bench_root = bench_root
        self.uptime_log = WindowedLogProvider("1w")
        self.request_log = WindowedLogProvider("24h")

    def get_all(self, site_names: Iterable[str]) -> dict[str, dict]:
        names = set(site_names)
        uptime = self.get_uptime(names)
        requests = self.get_requests(names)
        return {
            name: {
                "uptime_percent": self.get_percent(uptime[name]),
                "requests": requests[name],
            }
            for name in names
        }

    def get_uptime(self, names: set[str]) -> dict[str, list[bool]]:
        checks: dict[str, list[bool]] = {name: [] for name in names}
        path = self.bench_root / "logs" / "uptime.json.log"
        if not path.exists():
            return checks

        for record in self.uptime_log.records_in_window(path, time_key="time"):
            site, up = record.get("site"), record.get("up")
            if site in checks and isinstance(up, bool):
                checks[site].append(up)
        return checks

    def get_requests(self, names: set[str]) -> dict[str, list[int]]:
        counts = {name: [0] * REQUEST_BUCKETS for name in names}
        path = self.bench_root / "logs" / "monitor.json.log"
        if not path.exists():
            return counts

        for record in self.request_log.records_in_window(path, time_key="timestamp"):
            site = record.get("site")
            if site not in counts or record.get("transaction_type") != "request":
                continue
            if SiteMonitoringProvider.get_non_ping_path(record):
                counts[site][self.get_bucket(self.request_log, record.get("timestamp"), REQUEST_BUCKETS)] += 1
        return counts

    @staticmethod
    def get_bucket(log: WindowedLogProvider, value: object, count: int) -> int:
        when = log.get_time(value)
        elapsed = (when - log.cutoff).total_seconds() if when else 0
        return min(count - 1, max(0, int(elapsed * count // log.window_seconds)))

    @staticmethod
    def get_percent(checks: list[bool]) -> float | None:
        return round(sum(checks) / len(checks) * 100, 2) if checks else None
