from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from pilot.core.bench import Bench
from pilot.core.site.activity_rollup import SiteActivityRollup
from pilot.core.site.hourly_rollup import UptimeRollup


class SiteSummaryProvider:
    """Uptime and request summaries for every site, from the hourly rollups the monitor keeps."""

    def __init__(self, bench_root: Path) -> None:
        bench = Bench(bench_root)
        self.uptime = UptimeRollup(bench)
        self.activity = SiteActivityRollup(bench)

    def get_all(self, site_names: Iterable[str]) -> dict[str, dict]:
        """The uptime and request counts of each site."""
        return {
            name: {
                "uptime_percent": self.uptime.get_percent(name),
                "requests": self.activity.get_activity(name).requests,
            }
            for name in site_names
        }
