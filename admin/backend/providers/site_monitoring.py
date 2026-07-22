from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from admin.backend.internal.timeline import TimelinePoint, build_timeline
from admin.backend.providers.site_access_log import SiteAccessLogProvider
from admin.backend.providers.windowed_log import WindowedLogProvider
from pilot.core.site.uptime_monitoring import PING_PATH

_MAX_BUCKETS = 48
_TOP_LIMIT = 5


class SiteMonitoringProvider(WindowedLogProvider):
    """Aggregates one site's slice of Frappe's monitor.json.log for one time window.

    top_ips is a special case: monitor.json.log's IP comes from Frappe's own
    X-Forwarded-For parsing, which trusts an unvalidated, spoofable header. When
    the site has a real nginx access log (production, nginx in front), that's
    used instead - it's sourced from nginx's own trustworthy $remote_addr."""

    def __init__(self, bench_root: Path, site_name: str, window: str) -> None:
        super().__init__(window)
        self._log_path = bench_root / "logs" / "monitor.json.log"
        self._site_name = site_name
        self._bucket_seconds = max(60, self.window_seconds // _MAX_BUCKETS)
        self._access_log = SiteAccessLogProvider(bench_root, site_name, window)

    def get_analytics(self) -> dict:
        entries = list(self._entries_in_window())
        return {
            "window": self.window,
            "window_seconds": self.window_seconds,
            "now": self.now_ms(),
            "top_paths": self._timeline(entries, "request", self._non_ping_path, "count"),
            "slowest_requests": self._timeline(entries, "request", self._request_path, "duration"),
            "top_jobs": self._timeline(entries, "job", self._job_method, "count"),
            "slowest_jobs": self._timeline(entries, "job", self._job_method, "duration"),
            "top_ips": self._top_ips(entries),
            "slowest_reports": self._timeline(entries, "request", self._report_name, "duration"),
        }

    def _top_ips(self, entries: list[dict]) -> dict:
        if self._access_log.is_available():
            return self._access_log.get_top_ips()
        return self._timeline(entries, "request", self._request_ip, "count")

    def _timeline(
        self, entries: list[dict], transaction_type: str, category: Callable[[dict], str | None], by: str
    ) -> dict:
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
        """Entry example:
        {
            "duration": 845,
            "request": {
                "ip": "13.206.253.38",
                "method": "GET",
                "path": "/api/method/ping",
                "response_length": 18,
                "status_code": 200,
            },
            "site": "x.site.frappe.cloud",
            "timestamp": "2026-07-21 17:43:57.747746+00:00",
            "transaction_type": "request",
            "uuid": "xxx",
        }
        """
        return (entry.get("request") or {}).get("path")

    @classmethod
    def _non_ping_path(cls, entry: dict) -> str | None:
        """Uptime checks hit /api/method/ping constantly and would drown out real traffic."""
        path = cls._request_path(entry)
        return None if path == PING_PATH else path

    @staticmethod
    def _request_ip(entry: dict) -> str | None:
        path = SiteMonitoringProvider._request_path(entry)
        return None if path == PING_PATH else (entry.get("request") or {}).get("ip")

    @staticmethod
    def _job_method(entry: dict) -> str | None:
        return (entry.get("job") or {}).get("method")

    @staticmethod
    def _report_name(entry: dict) -> str | None:
        report = entry.get("report")
        return report if isinstance(report, str) and report else None

    def _entries_in_window(self):
        for record in self.records_in_window(self._log_path, time_key="timestamp"):
            if record.get("site") == self._site_name:
                yield record
