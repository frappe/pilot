"""Tests for cursor pagination on GET /api/v1/audit-events."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from pilot.config import BenchConfig


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    bench_root.mkdir(parents=True, exist_ok=True)
    (bench_root / "bench.toml").write_text(
        BenchConfig.from_flat(bench_root.name, {"admin_enabled": True, "admin_password": password}).dumps()
    )
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", Session(Bench(bench_root)).issue_session_token()[0])
    return client


def _mock_log(all_entries):
    log = Mock()
    log.entries.side_effect = lambda **kwargs: all_entries[: kwargs["limit"]]
    return log


def test_first_page_reports_a_next_cursor_when_more_remain(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    entries = [{"type": "site.create", "logged_at": str(i)} for i in range(5)]

    with patch("pilot.core.bench.audit_log.AuditLog", return_value=_mock_log(entries)):
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
    with patch("pilot.core.bench.audit_log.AuditLog", return_value=_mock_log(entries)):
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

    with patch("pilot.core.bench.audit_log.AuditLog", return_value=_mock_log(entries)):
        response = client.get("/api/v1/audit-events", query_string={"limit": 10000})

    body = response.get_json()
    assert body["meta"]["limit"] == 500
    assert len(body["data"]) == 500


def test_jti_query_param_is_passed_through_to_entries(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    log = _mock_log([])

    with patch("pilot.core.bench.audit_log.AuditLog", return_value=log):
        client.get("/api/v1/audit-events", query_string={"jti": "abc"})

    assert log.entries.call_args.kwargs["jti"] == "abc"


def test_an_invalid_cursor_is_treated_as_the_start(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    entries = [{"type": "site.create", "logged_at": str(i)} for i in range(3)]

    with patch("pilot.core.bench.audit_log.AuditLog", return_value=_mock_log(entries)):
        response = client.get(
            "/api/v1/audit-events", query_string={"limit": 2, "cursor": "not-a-real-cursor"}
        )

    assert response.get_json()["data"] == entries[:2]


def test_export_downloads_filtered_events_as_csv(tmp_path: Path) -> None:
    from pilot.core.bench import Bench
    from pilot.core.bench.audit_log import AuditLog

    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    log = AuditLog(Bench(bench_root))
    log.append(
        "backup",
        {
            "logged_at": "2026-10-27T09:21:56.999780+00:00",
            "site": "a.test",
            "event": "backup",
            "status": "success",
            "timestamp": "20261027_145156",
            "with_files": True,
            "offsite": False,
            "files": {"database.sql.gz": 270117, "files.tar": 1536},
            "pruned": [],
        },
    )
    log.append("ssh_key", {"event": "added"})

    response = client.get("/api/v1/audit-events/export", query_string={"type": "backup", "site": "a.test"})

    lines = response.get_data(as_text=True).splitlines()
    assert response.status_code == 200
    assert response.headers["Content-Disposition"] == 'attachment; filename="a.test-activity-log.csv"'
    assert lines[0] == "logged_at,type,event,site,app,status,actor,ip,command,task_id,details"
    assert lines[1:] == [
        "2026-10-27 09:21:56,backup,backup,a.test,,success,,,,,"
        '"timestamp=20261027_145156; with_files=True; offsite=False; files={""database.sql.gz"":270117,""files.tar"":1536}; pruned=[]"'
    ]


def test_export_keeps_only_events_inside_the_date_range(tmp_path: Path) -> None:
    from pilot.core.bench import Bench
    from pilot.core.bench.audit_log import AuditLog

    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    log = AuditLog(Bench(bench_root))
    for day in ("01", "02", "03"):
        log.append("app", {"event": f"day-{day}", "logged_at": f"2026-10-{day}T12:00:00+00:00"})

    response = client.get(
        "/api/v1/audit-events/export",
        query_string={"since": "2026-10-02T00:00:00+00:00", "until": "2026-10-02T23:59:59+00:00"},
    )

    rows = response.get_data(as_text=True).splitlines()[1:]
    assert [row.split(",")[2] for row in rows] == ["day-02"]


def test_a_date_without_an_offset_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    response = client.get("/api/v1/audit-events", query_string={"since": "2026-10-02T00:00:00"})

    assert response.status_code == 422
