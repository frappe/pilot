from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from pilot.core.bench import Bench
from pilot.core.site.hourly_rollup import RequestRollup, UptimeRollup


class SiteSummaryProvider:
    """Uptime and request summaries for every site, from the hourly rollups the monitor keeps."""

    def __init__(self, bench_root: Path) -> None:
        bench = Bench(bench_root)
        self.uptime = UptimeRollup(bench)
        self.requests = RequestRollup(bench)

    def get_all(self, site_names: Iterable[str]) -> dict[str, dict]:
        return {
            name: {
                "uptime_percent": self.uptime.get_percent(name),
                "requests": self.requests.get_counts(name),
            }
            for name in site_names
        }
