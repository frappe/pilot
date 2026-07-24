"""Tests for SiteUptimeProvider's aggregation of uptime.json.log."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from admin.backend.providers.site_uptime import SiteUptimeProvider


def _record(site: str, when: datetime, up: bool) -> dict:
    return {
        "time": when.isoformat(),
        "site": site,
        "up": up,
        "status_code": 200 if up else None,
        "response_ms": 50 if up else 5000,
    }


def _write_log(tmp_path: Path, records: list[dict]) -> Path:
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "uptime.json.log").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return tmp_path


def test_all_up_gives_hundred_percent(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    root = _write_log(tmp_path, [_record("site-a.local", now - timedelta(seconds=i * 5), True) for i in range(5)])

    result = SiteUptimeProvider(root, "site-a.local", "1h").get_uptime()

    assert result["overall_percent"] == 100.0
    assert all(bucket["percent"] == 100.0 for bucket in result["buckets"] if bucket["checks"])


def test_mixed_results_give_partial_percent(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    records = [_record("site-a.local", now - timedelta(seconds=i * 5), i % 2 == 0) for i in range(4)]
    root = _write_log(tmp_path, records)

    result = SiteUptimeProvider(root, "site-a.local", "1h").get_uptime()

    assert result["overall_percent"] == 50.0


def test_filters_by_site(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    root = _write_log(
        tmp_path,
        [
            _record("site-a.local", now, True),
            _record("site-b.local", now, False),
        ],
    )

    a = SiteUptimeProvider(root, "site-a.local", "1h").get_uptime()
    b = SiteUptimeProvider(root, "site-b.local", "1h").get_uptime()

    assert a["overall_percent"] == 100.0
    assert b["overall_percent"] == 0.0


def test_respects_window_cutoff(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    old = now - timedelta(hours=5)
    root = _write_log(
        tmp_path,
        [
            _record("site-a.local", old, False),
            _record("site-a.local", now, True),
        ],
    )

    result = SiteUptimeProvider(root, "site-a.local", "1h").get_uptime()

    assert result["overall_percent"] == 100.0


def test_no_data_gives_none_overall_and_empty_buckets(tmp_path: Path) -> None:
    result = SiteUptimeProvider(tmp_path, "site-a.local", "1h").get_uptime()

    assert result["overall_percent"] is None
    assert all(bucket["percent"] is None and bucket["checks"] == 0 for bucket in result["buckets"])


def test_buckets_cover_whole_window_even_with_short_history(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    root = _write_log(tmp_path, [_record("site-a.local", now - timedelta(seconds=i * 5), True) for i in range(5)])

    result = SiteUptimeProvider(root, "site-a.local", "1h").get_uptime()

    assert len(result["buckets"]) >= 60
    assert sum(bucket["checks"] for bucket in result["buckets"]) == 5
    assert result["buckets"][0]["percent"] is None


def test_bucket_seconds_scales_with_window(tmp_path: Path) -> None:
    short = SiteUptimeProvider(tmp_path, "site-a.local", "30m")
    long = SiteUptimeProvider(tmp_path, "site-a.local", "1w")

    assert short.bucket_seconds == 60  # floor, since 1800/60 < 60
    assert long.bucket_seconds == 604800 // 60


def test_production_enabled_false_when_no_bench_toml(tmp_path: Path) -> None:
    result = SiteUptimeProvider(tmp_path, "site-a.local", "1h").get_uptime()

    assert result["production_enabled"] is False


def test_production_enabled_true_when_bench_configured(tmp_path: Path) -> None:
    from pilot.config import BenchConfig

    data = {
        "bench": {"name": "test-bench", "python": "3.14"},
        "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
        "mariadb": {"root_password": "root"},
        "redis": {"cache_port": 13000, "queue_port": 11000},
        "production": {"enabled": True, "process_manager": "systemd"},
        "admin": {"domain": "admin.example.com"},
    }
    config = BenchConfig._from_dict(data)
    config.write(tmp_path)

    result = SiteUptimeProvider(tmp_path, "site-a.local", "1h").get_uptime()

    assert result["production_enabled"] is True


def test_malformed_entry_skipped(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    root = tmp_path
    logs = root / "logs"
    logs.mkdir()
    lines = [
        "not json at all",
        json.dumps({"time": now.isoformat(), "site": "site-a.local", "up": "not-a-bool"}),
        json.dumps(_record("site-a.local", now, True)),
    ]
    (logs / "uptime.json.log").write_text("\n".join(lines) + "\n")

    result = SiteUptimeProvider(root, "site-a.local", "1h").get_uptime()

    assert result["overall_percent"] == 100.0
