"""SlowQueryProvider bucketing, labeling, and db->site mapping."""

from __future__ import annotations

import json
from pathlib import Path

from admin.backend.providers.slow_queries import SlowQueryProvider


def _provider(bench_root: Path, window: str = "1h") -> SlowQueryProvider:
    return SlowQueryProvider(bench_root=bench_root, window=window)


def test_label_truncates_long_queries(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    short = "SELECT 1"
    long_query = "SELECT " + "a" * 100

    assert provider._label(short) == short
    label = provider._label(long_query)
    assert len(label) == 60
    assert label.endswith("…")


def test_bucket_starts_span_the_window(tmp_path: Path) -> None:
    provider = _provider(tmp_path, window="1h")
    buckets = provider._bucket_starts(bucket_seconds=300)

    assert buckets == sorted(buckets)
    assert buckets[-1] - buckets[0] <= 3600 * 1000
    assert all(b % (300 * 1000) == 0 for b in buckets)


def test_bucketed_sums_values_by_key_and_bucket(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    buckets = [0, 300_000]
    records = [
        {"time": "irrelevant", "site": "a"},
        {"time": "irrelevant", "site": "a"},
        {"time": "irrelevant", "site": "b"},
    ]
    # Bypass get_time by monkeypatching bucket assignment via a stub: simulate
    # two records landing in bucket 0 for site a, one in bucket 0 for site b.
    for record, when in zip(records, ["1970-01-01T00:00:00+00:00"] * 3, strict=True):
        record["time"] = when

    result = provider._bucketed(records, ["a", "b"], "site", buckets, 300, lambda r: 1)

    assert result[0] == {"bucket": 0, "a": 2, "b": 1}
    assert result[1] == {"bucket": 300_000, "a": 0, "b": 0}


def test_site_by_db_maps_db_name_to_site_dir(tmp_path: Path) -> None:
    site_dir = tmp_path / "sites" / "example.local"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({"db_name": "_abc123"}))

    provider = _provider(tmp_path)
    assert provider._site_by_db() == {"_abc123": "example.local"}
