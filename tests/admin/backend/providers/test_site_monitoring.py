"""Tests for SiteMonitoringProvider's aggregation of monitor.json.log."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from admin.backend.providers.site.monitoring import SiteMonitoringProvider
from pilot.config import BenchConfig
from pilot.core.database.slow_queries import SlowQueryLog


def _request(site: str, when: datetime, path: str, duration: int = 1_000_000) -> dict:
    return {
        "timestamp": when.isoformat(),
        "site": site,
        "transaction_type": "request",
        "duration": duration,
        "request": {"path": path, "ip": "1.2.3.4"},
    }


def _job(site: str, when: datetime, method: str, duration: int = 1_000_000) -> dict:
    return {
        "timestamp": when.isoformat(),
        "site": site,
        "transaction_type": "job",
        "duration": duration,
        "job": {"method": method},
    }


def _write_log(tmp_path: Path, records: list[dict]) -> Path:
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "monitor.json.log").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return tmp_path


def test_top_paths_ignores_uptime_ping(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    records = [_request("site-a.local", now - timedelta(seconds=i), "/api/method/ping") for i in range(10)]
    records.append(_request("site-a.local", now, "/app/todo"))
    root = _write_log(tmp_path, records)

    result = SiteMonitoringProvider(root, "site-a.local", "1h").get_analytics()

    assert result["top_paths"]["categories"] == ["/app/todo"]


def test_slowest_requests_still_includes_ping(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    root = _write_log(tmp_path, [_request("site-a.local", now, "/api/method/ping", duration=9_000_000)])

    result = SiteMonitoringProvider(root, "site-a.local", "1h").get_analytics()

    assert result["slowest_requests"]["categories"] == ["/api/method/ping"]


def test_requests_over_time_counts_all_non_ping_requests(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    records = [_request("site-a.local", now, "/api/method/ping")]
    records += [_request("site-a.local", now, "/app/todo") for _ in range(3)]
    records.append(_request("site-a.local", now, "/app/other"))
    root = _write_log(tmp_path, records)

    result = SiteMonitoringProvider(root, "site-a.local", "1h").get_analytics()

    assert result["requests_over_time"]["categories"] == ["Requests"]
    assert sum(p["Requests"] for p in result["requests_over_time"]["points"]) == 4


def test_requests_over_time_zero_fills_empty_buckets(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    root = _write_log(tmp_path, [_request("site-a.local", now, "/app/todo")])

    result = SiteMonitoringProvider(root, "site-a.local", "30m").get_analytics()

    points = result["requests_over_time"]["points"]
    assert len(points) > 1
    assert sum(p["Requests"] for p in points) == 1
    assert any(p["Requests"] == 0 for p in points)


def test_avg_request_duration_does_not_zero_fill(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    root = _write_log(tmp_path, [_request("site-a.local", now, "/app/todo", duration=2_000_000)])

    result = SiteMonitoringProvider(root, "site-a.local", "30m").get_analytics()

    points = result["avg_request_duration"]["points"]
    populated = [p for p in points if "/app/todo" in p]
    assert len(points) > 1
    assert populated == [{"time": populated[0]["time"], "/app/todo": 2.0}]


def test_avg_request_duration_breaks_down_by_path(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    records = [
        _request("site-a.local", now, "/app/a", duration=1_000_000),
        _request("site-a.local", now, "/app/a", duration=3_000_000),
        _request("site-a.local", now, "/app/b", duration=6_000_000),
    ]
    root = _write_log(tmp_path, records)

    result = SiteMonitoringProvider(root, "site-a.local", "1h").get_analytics()

    point = next(p for p in result["avg_request_duration"]["points"] if "/app/a" in p)
    assert point["/app/a"] == 2.0
    assert point["/app/b"] == 6.0


def test_background_jobs_over_time_and_avg_duration(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    records = [
        _job("site-a.local", now, "app.tasks.a", duration=2_000_000),
        _job("site-a.local", now, "app.tasks.a", duration=4_000_000),
        _job("site-a.local", now, "app.tasks.b", duration=8_000_000),
    ]
    root = _write_log(tmp_path, records)

    result = SiteMonitoringProvider(root, "site-a.local", "1h").get_analytics()

    assert sum(p["Jobs"] for p in result["background_jobs_over_time"]["points"]) == 3
    point = next(p for p in result["avg_job_duration"]["points"] if "app.tasks.a" in p)
    assert point["app.tasks.a"] == 3.0
    assert point["app.tasks.b"] == 8.0


def _site_root(tmp_path: Path, site: str, db_name: str) -> Path:
    site_dir = tmp_path / "sites" / site
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({"db_name": db_name}))
    return tmp_path


def test_slow_query_timelines_scoped_to_site_db(tmp_path: Path) -> None:
    root = _site_root(tmp_path, "site-a.local", "_dba")
    config = BenchConfig.default(name="test-bench")
    slow_query_log_file = tmp_path / "slow-queries.json"
    now = datetime.now(UTC)
    SlowQueryLog(slow_query_log_file).append([
        {"db": "_dba", "sql_text": "SELECT 1", "query_time": 1.5, "start_time": now},
        {"db": "_dba", "sql_text": "SELECT 1", "query_time": 1.5, "start_time": now},
        {"db": "_other_site_db", "sql_text": "SELECT 2", "query_time": 9.0, "start_time": now},
    ])

    with (
        patch.object(BenchConfig, "read", return_value=config),
        patch("pilot.config.monitor.slow_query_log_path", return_value=slow_query_log_file),
    ):
        result = SiteMonitoringProvider(root, "site-a.local", "1h").get_analytics()

    assert result["frequent_slow_queries"]["categories"] == ["SELECT ?"]
    assert result["slowest_queries"]["categories"] == ["SELECT ?"]
    point = next(p for p in result["slowest_queries"]["points"] if "SELECT ?" in p)
    assert point["SELECT ?"] == 1.5


def test_slow_query_timeline_empty_for_unknown_site(tmp_path: Path) -> None:
    root = _write_log(tmp_path, [])

    result = SiteMonitoringProvider(root, "no-such-site.local", "1h").get_analytics()

    assert result["frequent_slow_queries"]["categories"] == []
