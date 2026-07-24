"""Tests for SiteMonitoringProvider's aggregation of monitor.json.log."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from admin.backend.providers.site_monitoring import SiteMonitoringProvider
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


def _site_root(tmp_path: Path, site: str, db_name: str) -> Path:
    site_dir = tmp_path / "sites" / site
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({"db_name": db_name}))
    return tmp_path


def test_slow_query_timelines_scoped_to_site_db(tmp_path: Path) -> None:
    root = _site_root(tmp_path, "site-a.local", "_dba")
    config = BenchConfig.default(name="test-bench")
    config.monitor.slow_query_log_path = tmp_path / "slow-queries.json"
    now = datetime.now(UTC)
    SlowQueryLog(config.monitor.slow_query_log_path).append([
        {"db": "_dba", "sql_text": "SELECT 1", "query_time": 1.5, "start_time": now},
        {"db": "_dba", "sql_text": "SELECT 1", "query_time": 1.5, "start_time": now},
        {"db": "_other_site_db", "sql_text": "SELECT 2", "query_time": 9.0, "start_time": now},
    ])

    with patch.object(BenchConfig, "read", return_value=config):
        result = SiteMonitoringProvider(root, "site-a.local", "1h").get_analytics()

    assert result["frequent_slow_queries"]["categories"] == ["SELECT ?"]
    assert result["slowest_queries"]["categories"] == ["SELECT ?"]
    assert result["slowest_queries"]["points"][0]["SELECT ?"] == 1.5


def test_slow_query_timeline_empty_for_unknown_site(tmp_path: Path) -> None:
    root = _write_log(tmp_path, [])

    result = SiteMonitoringProvider(root, "no-such-site.local", "1h").get_analytics()

    assert result["frequent_slow_queries"]["categories"] == []
