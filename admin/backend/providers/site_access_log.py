from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from admin.backend.internal.timeline import TimelinePoint, build_timeline
from admin.backend.providers.windowed_log import WindowedLogProvider

_MAX_BUCKETS = 48
_TOP_LIMIT = 5

_LINE_RE = re.compile(
    r"^(?P<ip>\S+) \[(?P<time>[^\]]+)\] "
    r'"(?P<method>\S+) (?P<uri>\S*)" (?P<status>\d{3}) '
    r'"(?P<host>[^"]*)" (?P<request_time>\S+)\s*$'
)
_TIME_FORMAT = "%d/%b/%Y:%H:%M:%S %z"


class SiteAccessLogProvider(WindowedLogProvider):
    """Aggregates one site's slice of the shared nginx access log - real,
    non-spoofable client IPs (nginx's own $remote_addr) - for one time window."""

    def __init__(self, bench_root: Path, site_name: str, window: str) -> None:
        super().__init__(window)
        self._log_path = bench_root / "logs" / "nginx-access.log"
        self._site_name = site_name
        self._bucket_seconds = max(60, self.window_seconds // _MAX_BUCKETS)

    def is_available(self) -> bool:
        return self._log_path.exists()

    def get_top_ips(self) -> dict:
        return build_timeline(self._points(), _TOP_LIMIT, self._bucket_seconds, "count")

    def _points(self) -> list[TimelinePoint]:
        points = []
        for line in self._iter_lines_reversed(self._log_path):
            match = _LINE_RE.match(line.decode("utf-8", errors="replace"))
            if match is None:
                continue
            when = self._parse_time(match["time"])
            if when is None:
                continue
            # break here regardless of host, not just on our own lines.
            if when < self.cutoff:
                break
            if match["host"] != self._site_name:
                continue
            duration = self._parse_duration(match["request_time"])
            points.append(TimelinePoint(self.to_epoch_ms(when), match["ip"], duration))
        return points

    @staticmethod
    def _parse_time(value: str) -> datetime | None:
        try:
            return datetime.strptime(value, _TIME_FORMAT)
        except ValueError:
            return None

    @staticmethod
    def _parse_duration(value: str) -> int:
        try:
            return int(float(value) * 1_000_000)
        except ValueError:
            return 0
