from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from admin.backend.internal.timeline import TimelinePoint, build_timeline
from admin.backend.providers.windowed_log import WindowedLogProvider
from pilot.core.site.uptime_monitoring import PING_PATH
from pilot.internal.jsonl_log import JsonLines


class SiteAccessLogProvider(WindowedLogProvider):
    """Aggregates one site's slice of the shared nginx access log - real,
    non-spoofable client IPs (nginx's own $remote_addr) - for one time window."""

    line_re = re.compile(
    r"^(?P<ip>\S+) \[(?P<time>[^\]]+)\] "
    r'"(?P<method>\S+) (?P<uri>\S*)" (?P<status>\d{3}) '
    r'"(?P<host>[^"]*)" (?P<request_time>\S+)\s*$'
)

    def __init__(self, bench_root: Path, site_name: str, window: str) -> None:
        super().__init__(window)
        self._log_path = bench_root / "logs" / "nginx-access.log"
        self._site_name = site_name
        self._bucket_seconds = max(60, self.window_seconds // 30)

    def is_available(self) -> bool:
        """Whether the access log exists."""
        return self._log_path.exists()

    def get_top_ips(self) -> dict:
        """The top client IPs over time."""
        return build_timeline(
            self._points(), 5, self._bucket_seconds, "count", self.to_epoch_ms(self.cutoff), self.now_ms()
        )

    def _points(self) -> list[TimelinePoint]:
        """The access log points in the window."""
        points = []
        for line in JsonLines(self._log_path).read_lines_newest_first():
            match = self.line_re.match(line.decode("utf-8", errors="replace"))
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
            # Uptime checks hit the ping endpoint constantly and would drown out real client IPs.
            if match["uri"].split("?", 1)[0] == PING_PATH:
                continue
            duration = self._parse_duration(match["request_time"])
            points.append(TimelinePoint(self.to_epoch_ms(when), match["ip"], duration))
        return points

    @staticmethod
    def _parse_time(value: str) -> datetime | None:
        """The time of a log value, or None."""
        try:
            return datetime.strptime(value, "%d/%b/%Y:%H:%M:%S %z")
        except ValueError:
            return None

    @staticmethod
    def _parse_duration(value: str) -> int:
        """The duration in microseconds, or 0."""
        try:
            return int(float(value) * 1_000_000)
        except ValueError:
            return 0
