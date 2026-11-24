from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from admin.backend.internal.timeline import TimelinePoint, build_timeline, build_total_timeline
from admin.backend.providers.site.access_log import SiteAccessLogProvider
from admin.backend.providers.windowed_log import WindowedLogProvider
from pilot.core.site.uptime_monitoring import PING_PATH
from pilot.internal.jsonl_log import JsonLines
from pilot.internal.site_paths import site_config_path


class SiteMonitoringProvider(WindowedLogProvider):
    """Aggregates one site's slice of Frappe's monitor.json.log for one time window.

    top_ips is a special case: monitor.json.log's IP comes from Frappe's own
    X-Forwarded-For parsing, which trusts an unvalidated, spoofable header. When
    the site has a real nginx access log (production, nginx in front), that's
    used instead - it's sourced from nginx's own trustworthy $remote_addr."""

    top_limit = 5

    def __init__(self, bench_root: Path, site_name: str, window: str) -> None:
        super().__init__(window)
        self._bench_root = bench_root
        self._log_path = bench_root / "logs" / "monitor.json.log"
        self._site_name = site_name
        self._bucket_seconds = max(60, self.window_seconds // 30)
        self._access_log = SiteAccessLogProvider(bench_root, site_name, window)
        self._db_name = self._read_db_name(bench_root, site_name)
        self._start_ms = self.to_epoch_ms(self.cutoff)
        self._end_ms = self.now_ms()

    def get_analytics(self) -> dict:
        """The request, job, report and query timelines."""
        points = self.get_points(self._entries_in_window())
        return {
            "window": self.window,
            "window_seconds": self.window_seconds,
            "now": self._end_ms,
            "requests_over_time": self.get_total(points["paths"], "Requests"),
            "top_paths": self.get_timeline(points["paths"], "count"),
            "slowest_requests": self.get_timeline(points["all_paths"], "duration"),
            "avg_request_duration": self.get_timeline(points["paths"], "average"),
            "background_jobs_over_time": self.get_total(points["jobs"], "Jobs"),
            "top_jobs": self.get_timeline(points["jobs"], "count"),
            "slowest_jobs": self.get_timeline(points["jobs"], "duration"),
            "avg_job_duration": self.get_timeline(points["jobs"], "average"),
            "top_ips": self._top_ips(points["ips"]),
            "slowest_reports": self.get_timeline(points["reports"], "duration"),
            "frequent_slow_queries": self._slow_query_timeline("count"),
            "slowest_queries": self._slow_query_timeline("duration"),
        }

    def get_timeline(self, points: list[TimelinePoint], by: str) -> dict:
        """The top categories of the points over the window."""
        return build_timeline(points, self.top_limit, self._bucket_seconds, by, self._start_ms, self._end_ms)

    def get_total(self, points: list[TimelinePoint], series_name: str) -> dict:
        """The count of the points over the window."""
        return build_total_timeline(points, self._bucket_seconds, series_name, self._start_ms, self._end_ms)

    @classmethod
    def get_points(cls, entries: Iterable[tuple[int, dict]]) -> dict[str, list[TimelinePoint]]:
        """Every point list the timelines need, in one pass over the entries."""
        categories = {
            "request": {
                "paths": cls.get_non_ping_path,
                "all_paths": cls._request_path,
                "ips": cls._request_ip,
                "reports": cls._report_name,
            },
            "job": {"jobs": cls._job_method},
        }
        points: dict[str, list[TimelinePoint]] = {key: [] for group in categories.values() for key in group}
        for time_ms, entry in entries:
            duration = entry.get("duration")
            if not isinstance(duration, (int, float)):
                continue
            for key, category in categories.get(entry.get("transaction_type"), {}).items():
                if name := category(entry):
                    points[key].append(TimelinePoint(time_ms, name, duration))
        return points

    def _slow_query_timeline(self, by: str) -> dict:
        """The slow query timeline of the site."""
        from pilot.config.monitor import slow_query_log_path
        from pilot.core.database.slow_queries import SlowQueryLog

        if not self._db_name:
            points = []
        else:
            points = [
                TimelinePoint(
                    self.to_epoch_ms(when), record["query"], (record.get("query_time") or 0) * 1_000_000
                )
                for record in SlowQueryLog(slow_query_log_path()).records()
                if record.get("db") == self._db_name
                and (when := self.get_time(record.get("time"))) is not None
                and when >= self.cutoff
            ]
        return self.get_timeline(points, by)

    @staticmethod
    def _read_db_name(bench_root: Path, site_name: str) -> str | None:
        """The site's database name, if any."""
        config_path = site_config_path(bench_root, site_name)
        if not config_path:
            return None
        try:
            return json.loads(config_path.read_text()).get("db_name")
        except (OSError, ValueError):
            return None

    def _top_ips(self, points: list[TimelinePoint]) -> dict:
        """The top client IPs."""
        if self._access_log.is_available():
            return self._access_log.get_top_ips()
        return self.get_timeline(points, "count")

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
    def get_non_ping_path(cls, entry: dict) -> str | None:
        """Uptime checks hit /api/method/ping constantly and would drown out real traffic."""
        path = cls._request_path(entry)
        return None if path == PING_PATH else path

    @staticmethod
    def _request_ip(entry: dict) -> str | None:
        """The client IP of a request, skipping uptime pings."""
        path = SiteMonitoringProvider._request_path(entry)
        return None if path == PING_PATH else (entry.get("request") or {}).get("ip")

    @staticmethod
    def _job_method(entry: dict) -> str | None:
        """The method of a job."""
        return (entry.get("job") or {}).get("method")

    @staticmethod
    def _report_name(entry: dict) -> str | None:
        """The name of a report."""
        report = entry.get("report")
        return report if isinstance(report, str) and report else None

    def _entries_in_window(self) -> Iterator[tuple[int, dict]]:
        """The site's monitor entries in the window as (epoch ms, entry), newest first. Frappe writes
        compact JSON, so the time and site are read from the raw line and only this site's lines are
        parsed; a line in another layout is parsed whole."""
        site_marker = b'"site":' + json.dumps(self._site_name).encode()
        for line in JsonLines(self._log_path).read_lines_newest_first():
            raw_time = self.read_raw_value(line, b'"timestamp":"')
            record = None if raw_time else JsonLines.parse(line)
            when = self.get_time(record.get("timestamp") if record else raw_time)
            if when is None:
                continue
            if when < self.cutoff:
                break
            if record is None:
                if b'"site":"' in line and site_marker not in line:
                    continue
                record = JsonLines.parse(line)
            if record is not None and record.get("site") == self._site_name:
                yield self.to_epoch_ms(when), record

    @staticmethod
    def read_raw_value(line: bytes, key: bytes) -> str | None:
        """The string value after a compact JSON key, or None when the line lacks it."""
        start = line.find(key)
        if start < 0:
            return None
        start += len(key)
        end = line.find(b'"', start)
        return line[start:end].decode() if end > start else None
