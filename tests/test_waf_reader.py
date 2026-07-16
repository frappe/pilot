"""Tests for WafReader aggregation of the ModSecurity JSON audit log."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from admin.backend.readers.waf_reader import WafReader


def _entry(ip: str, code: int, rules: list[tuple[str, str]], when: datetime) -> dict:
    return {"transaction": {
        "client_ip": ip,
        "time_stamp": when.strftime("%a %b %d %H:%M:%S %Y"),
        "request": {"method": "GET", "uri": "/"},
        "response": {"http_code": code},
        "messages": [{"message": msg, "details": {"ruleId": rid, "msg": msg, "tags": ["attack"]}} for rid, msg in rules],
    }}


def _write_log(tmp_path: Path, entries: list[dict]) -> Path:
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "modsec_audit.log").write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return tmp_path


def test_totals_and_classification(tmp_path: Path) -> None:
    now = datetime.now()
    root = _write_log(tmp_path, [
        _entry("1.1.1.1", 200, [("942100", "SQL Injection")], now),
        _entry("1.1.1.1", 200, [("941100", "XSS")], now),
        _entry("2.2.2.2", 403, [("942100", "SQL Injection"), ("949110", "Inbound Anomaly Score Exceeded")], now),
        _entry("2.2.2.2", 200, [("949110", "Inbound Anomaly Score Exceeded")], now),
        {"transaction": {"client_ip": "3.3.3.3", "time_stamp": now.strftime("%a %b %d %H:%M:%S %Y"), "messages": []}},
    ])

    out = WafReader(root, "24h").read()

    assert out["totals"] == {"flagged": 4, "blocked": 1, "would_block": 2}
    assert out["log_present"] is True


def test_top_rules_excludes_scoring_rules(tmp_path: Path) -> None:
    now = datetime.now()
    root = _write_log(tmp_path, [
        _entry("1.1.1.1", 200, [("942100", "SQL Injection")], now),
        _entry("1.1.1.1", 200, [("942100", "SQL Injection"), ("949110", "Score")], now),
        _entry("1.1.1.1", 200, [("941100", "XSS")], now),
    ])

    top = {r["id"]: r["count"] for r in WafReader(root, "24h").read()["top_rules"]}
    assert top == {"942100": 2, "941100": 1}  # 949110 filtered out


def test_top_ips_counts(tmp_path: Path) -> None:
    now = datetime.now()
    root = _write_log(tmp_path, [
        _entry("2.2.2.2", 200, [("942100", "SQLi")], now),
        _entry("2.2.2.2", 200, [("942100", "SQLi")], now),
        _entry("1.1.1.1", 200, [("942100", "SQLi")], now),
    ])
    top = WafReader(root, "24h").read()["top_ips"]
    assert top[0] == {"ip": "2.2.2.2", "count": 2}


def test_window_excludes_old_entries(tmp_path: Path) -> None:
    now = datetime.now()
    old = now - timedelta(hours=5)
    # ModSecurity appends chronologically (oldest first); the reader walks from
    # the end and stops once it passes the window boundary.
    root = _write_log(tmp_path, [
        _entry("9.9.9.9", 200, [("942100", "old")], old),
        _entry("1.1.1.1", 200, [("942100", "recent")], now),
    ])

    out = WafReader(root, "1h").read()
    assert out["totals"]["flagged"] == 1


def test_missing_log_is_empty(tmp_path: Path) -> None:
    out = WafReader(tmp_path, "1h").read()
    assert out["totals"] == {"flagged": 0, "blocked": 0, "would_block": 0}
    assert out["log_present"] is False


def test_malformed_lines_skipped(tmp_path: Path) -> None:
    now = datetime.now()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "modsec_audit.log").write_text(
        "not json\n" + json.dumps(_entry("1.1.1.1", 200, [("942100", "SQLi")], now)) + "\n{broken\n"
    )
    assert WafReader(tmp_path, "24h").read()["totals"]["flagged"] == 1


def test_alternate_field_names(tmp_path: Path) -> None:
    # The older "suggested" schema: 'client ip', 'data', 'id', ISO timestamp.
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logs = tmp_path / "logs"
    logs.mkdir()
    entry = {"transaction": {
        "client ip": "5.5.5.5", "timestamp": now,
        "response": {"http_code": 200},
        "messages": [{"message": "XSS", "data": {"id": "941100", "msg": "XSS"}}],
    }}
    (logs / "modsec_audit.log").write_text(json.dumps(entry) + "\n")

    out = WafReader(tmp_path, "24h").read()
    assert out["totals"]["flagged"] == 1
    assert out["top_ips"][0]["ip"] == "5.5.5.5"
    assert out["top_rules"][0]["id"] == "941100"
