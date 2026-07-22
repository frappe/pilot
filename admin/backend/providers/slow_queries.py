from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from admin.backend.providers.windowed_log import WindowedLogProvider

# Bucket size scales with the window so short ranges stay at fine granularity
# while long ranges (24h/1w) don't explode into thousands of buckets.
_BUCKET_SECONDS = [(3600, 300), (86400, 3600), (float("inf"), 86400)]
_TOP_QUERIES = 6
_QUERY_LABEL_LEN = 60


class SlowQueryProvider(WindowedLogProvider):
    """Reads the slow-query occurrence log for a window, bucketed per site so
    the dashboard can show which site is causing trouble over time."""

    def __init__(self, bench_root: Path | str, window: str = "1h") -> None:
        super().__init__(window)
        self._bench_root = Path(bench_root)

    def get_overview(self) -> dict:
        from pilot.config import BenchConfig
        from pilot.core.database import make_database
        from pilot.core.database.engines import MariaDB
        from pilot.core.database.slow_queries import SlowQueryLog

        config = BenchConfig.read(self._bench_root)
        if config.db_type != "mariadb":
            return {"enabled": False, "unsupported": True, "sites": [], "counts": [], "durations": []}
        database = make_database(config)
        if not isinstance(database, MariaDB) or not database.is_slow_log_enabled():
            return {"enabled": False, "sites": [], "counts": [], "durations": []}

        site_by_db = self._site_by_db()
        records = [
            {
                **record,
                "site": site_by_db.get(str(record.get("db") or ""), str(record.get("db") or "unknown")),
                "query": self._label(record.get("query") or ""),
            }
            for record in SlowQueryLog(config.monitor.slow_query_log_path).records()
            if (when := self.get_time(record.get("time"))) and when >= self.cutoff
        ]
        sites = sorted({r["site"] for r in records})
        queries = [q for q, _ in Counter(r["query"] for r in records).most_common(_TOP_QUERIES)]
        bucket_seconds = next(seconds for cutoff, seconds in _BUCKET_SECONDS if self.window_seconds <= cutoff)
        buckets = self._bucket_starts(bucket_seconds)
        return {
            "enabled": True,
            "sites": sites,
            "counts": self._bucketed(records, sites, "site", buckets, bucket_seconds, lambda r: 1),
            "durations": self._bucketed(records, sites, "site", buckets, bucket_seconds, lambda r: r.get("query_time") or 0),
            "queries": queries,
            "query_counts": self._bucketed(records, queries, "query", buckets, bucket_seconds, lambda r: 1),
        }

    def _bucket_starts(self, bucket_seconds: int) -> list[int]:
        bucket_ms = bucket_seconds * 1000
        start = self.to_epoch_ms(self.cutoff) // bucket_ms * bucket_ms
        end = self.now_ms() // bucket_ms * bucket_ms
        return list(range(start, end + bucket_ms, bucket_ms))

    def _bucketed(self, records: list[dict], keys: list[str], field: str, buckets: list[int], bucket_seconds: int, value_of) -> list[dict]:
        bucket_ms = bucket_seconds * 1000
        totals = {(bucket, key): 0.0 for bucket in buckets for key in keys}
        for record in records:
            when = self.get_time(record.get("time"))
            if when is None:
                continue
            bucket = self.to_epoch_ms(when) // bucket_ms * bucket_ms
            cell = (bucket, record[field])
            if cell in totals:
                totals[cell] += value_of(record)
        return [{"bucket": bucket, **{key: round(totals[(bucket, key)], 2) for key in keys}} for bucket in buckets]

    @staticmethod
    def _label(query: str) -> str:
        return query if len(query) <= _QUERY_LABEL_LEN else query[: _QUERY_LABEL_LEN - 1] + "…"

    def _site_by_db(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        sites_dir = self._bench_root / "sites"
        if not sites_dir.is_dir():
            return mapping
        for site_dir in sites_dir.iterdir():
            config_path = site_dir / "site_config.json"
            if not config_path.exists():
                continue
            try:
                db_name = json.loads(config_path.read_text()).get("db_name")
            except (OSError, ValueError):
                continue
            if db_name:
                mapping[db_name] = site_dir.name
        return mapping
