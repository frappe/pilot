"""Tests for cursor pagination on GET /api/v1/audit-events."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from pilot.config.bench_toml_builder import BenchTomlBuilder


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from pilot.commands.generate_session import ensure_jwt_secret, issue_token

    bench_root.mkdir(parents=True, exist_ok=True)
    (bench_root / "bench.toml").write_text(
        BenchTomlBuilder(bench_root.name, {"admin_enabled": True, "admin_password": password}).render()
    )
    secret = ensure_jwt_secret(bench_root / "bench.toml")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))
    return client


def _mock_log(all_entries):
    log = Mock()
    log.entries.side_effect = lambda **kwargs: all_entries[: kwargs["limit"]]
    return log


def test_first_page_reports_a_next_cursor_when_more_remain(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    entries = [{"type": "site.create", "logged_at": str(i)} for i in range(5)]

    with patch("pilot.core.audit_log.AuditLog", return_value=_mock_log(entries)):
        response = client.get("/api/v1/audit-events", query_string={"limit": 2})

    body = response.get_json()
    assert response.status_code == 200
    assert body["data"] == entries[:2]
    assert body["meta"]["limit"] == 2
    assert body["meta"]["next_cursor"]


def test_following_the_cursor_walks_the_full_log(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    entries = [{"type": "site.create", "logged_at": str(i)} for i in range(5)]

    collected = []
    cursor = None
    with patch("pilot.core.audit_log.AuditLog", return_value=_mock_log(entries)):
        for _ in range(10):
            params = {"limit": 2}
            if cursor:
                params["cursor"] = cursor
            response = client.get("/api/v1/audit-events", query_string=params)
            body = response.get_json()
            collected.extend(body["data"])
            cursor = body["meta"]["next_cursor"]
            if not cursor:
                break

    assert collected == entries
    assert cursor is None


def test_limit_is_capped_at_the_hard_maximum(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    entries = [{"type": "site.create", "logged_at": str(i)} for i in range(600)]

    with patch("pilot.core.audit_log.AuditLog", return_value=_mock_log(entries)):
        response = client.get("/api/v1/audit-events", query_string={"limit": 10000})

    body = response.get_json()
    assert body["meta"]["limit"] == 500
    assert len(body["data"]) == 500


def test_an_invalid_cursor_is_treated_as_the_start(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    entries = [{"type": "site.create", "logged_at": str(i)} for i in range(3)]

    with patch("pilot.core.audit_log.AuditLog", return_value=_mock_log(entries)):
        response = client.get(
            "/api/v1/audit-events", query_string={"limit": 2, "cursor": "not-a-real-cursor"}
        )

    assert response.get_json()["data"] == entries[:2]
