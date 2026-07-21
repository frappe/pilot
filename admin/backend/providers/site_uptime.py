from __future__ import annotations

from pathlib import Path

from admin.backend.providers.windowed_log import WindowedLogProvider

_MAX_BUCKETS = 60


class SiteUptimeProvider(WindowedLogProvider):
    """Aggregates one site's slice of the shared per-bench uptime.json.log
    (pilot.core.site.uptime_monitoring) into per-bucket availability
    percentages, plus one overall percentage for the whole window.

    Unlike SiteMonitoringProvider's metrics, uptime has no categories to rank
    - it's a single series (this site, up or down) - so it doesn't use
    build_timeline; each bucket's value is a percentage, not a count."""

    def __init__(self, bench_root: Path, site_name: str, window: str) -> None:
        super().__init__(window)
        self._bench_root = bench_root
        self._log_path = bench_root / "logs" / "uptime.json.log"
        self._site_name = site_name
        self.bucket_seconds = max(60, self.window_seconds // _MAX_BUCKETS)

    def get_uptime(self) -> dict:
        entries = list(self._entries_in_window())
        return {
            "window": self.window,
            "window_seconds": self.window_seconds,
            "bucket_seconds": self.bucket_seconds,
            "now": self.now_ms(),
            "overall_percent": self._percent(entries),
            "buckets": self._buckets(entries),
            # Site pings only run for production-enabled benches
            # (pilot.core.site.uptime_monitoring._production_uptime_monitors)
            # - dev benches never get data, no matter how long you wait.
            "production_enabled": self._is_production_enabled(),
        }

    def _is_production_enabled(self) -> bool:
        from pilot.config import BenchConfig

        try:
            return BenchConfig.read(self._bench_root).production.enabled
        except Exception:
            return False

    def _buckets(self, entries: list[dict]) -> list[dict]:
        bucket_ms = self.bucket_seconds * 1000
        grouped: dict[int, list[dict]] = {}
        for entry in entries:
            when = self.get_time(entry.get("time"))
            if when is None or not isinstance(entry.get("up"), bool):
                continue
            bucket_start = self.to_epoch_ms(when) // bucket_ms * bucket_ms
            grouped.setdefault(bucket_start, []).append(entry)
        return [
            {
                "time": start,
                "percent": self._percent(bucket_entries),
                "checks": len(bucket_entries),
            }
            for start, bucket_entries in sorted(grouped.items())
        ]

    @staticmethod
    def _percent(entries: list[dict]) -> float | None:
        ups = [entry["up"] for entry in entries if isinstance(entry.get("up"), bool)]
        if not ups:
            return None
        return round(sum(ups) / len(ups) * 100, 2)

    def _entries_in_window(self) -> list[dict]:
        return [
            record
            for record in self.records_in_window(self._log_path, time_key="time")
            if record.get("site") == self._site_name
        ]
