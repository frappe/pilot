from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from admin.backend.providers.timeline import TimelinePoint, build_timeline
from admin.backend.providers.windowed_log import WindowedLogProvider

_MAX_BUCKETS = 48
_TOP_LIMIT = 5


class SiteMonitoringProvider(WindowedLogProvider):
    """Aggregates one site's slice of Frappe's monitor.json.log for one time window."""

    def __init__(self, bench_root: Path, site_name: str, window: str) -> None:
        super().__init__(window)
        self._log_path = bench_root / "logs" / "monitor.json.log"
        self._site_name = site_name
        self._bucket_seconds = max(60, self.window_seconds // _MAX_BUCKETS)

    def get_analytics(self) -> dict:
        entries = list(self._entries_in_window())
        return {
            "window": self.window,
            "window_seconds": self.window_seconds,
            "now": self.now_ms(),
            "top_paths": self._timeline(entries, "request", self._request_path, "count"),
            "slowest_requests": self._timeline(entries, "request", self._request_path, "duration"),
            "top_jobs": self._timeline(entries, "job", self._job_method, "count"),
            "slowest_jobs": self._timeline(entries, "job", self._job_method, "duration"),
            "top_ips": self._timeline(entries, "request", self._request_ip, "count"),
        }

    def _timeline(self, entries: list[dict], transaction_type: str, category: Callable[[dict], str | None], by: str) -> dict:
        points = self._points(entries, transaction_type, category)
        return build_timeline(points, _TOP_LIMIT, self._bucket_seconds, by)

    def _points(
        self, entries: list[dict], transaction_type: str, category: Callable[[dict], str | None]
    ) -> list[TimelinePoint]:
        points = []
        for entry in entries:
            if entry.get("transaction_type") != transaction_type:
                continue
            duration = entry.get("duration")
            name = category(entry)
            when = self.get_time(entry.get("timestamp"))
            if when is None or not isinstance(duration, (int, float)) or not name:
                continue
            points.append(TimelinePoint(self.to_epoch_ms(when), name, duration))
        return points

    @staticmethod
    def _request_path(entry: dict) -> str | None:
        return (entry.get("request") or {}).get("path")

    @staticmethod
    def _request_ip(entry: dict) -> str | None:
        return (entry.get("request") or {}).get("ip")

    @staticmethod
    def _job_method(entry: dict) -> str | None:
        return (entry.get("job") or {}).get("method")

    def _entries_in_window(self):
        for record in self.records_in_window(self._log_path, time_key="timestamp"):
            if record.get("site") == self._site_name:
                yield record
