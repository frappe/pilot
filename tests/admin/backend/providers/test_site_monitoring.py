"""Tests for SiteMonitoringProvider's aggregation of monitor.json.log."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from admin.backend.providers.site_monitoring import SiteMonitoringProvider


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
