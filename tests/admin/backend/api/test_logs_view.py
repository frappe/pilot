"""Tests for /api/v1/logs routes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

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


def _make_log(bench_root: Path, name: str, content: str) -> None:
    logs_dir = bench_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / name).write_text(content)


def test_logs_list_and_read(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_log(bench_root, "web.log", "line1\nline2\n")
    client = _client(bench_root)

    listing = client.get("/api/v1/logs")
    detail = client.get("/api/v1/logs/web.log")

    assert listing.status_code == 200
    assert listing.get_json()[0]["filename"] == "web.log"
    assert detail.status_code == 200
    assert detail.get_json()["lines"] == ["line1", "line2"]


def test_log_content_serves_the_raw_file(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_log(bench_root, "web.log", "hello\n")
    client = _client(bench_root)

    response = client.get("/api/v1/logs/web.log/content")

    assert response.status_code == 200
    assert response.data == b"hello\n"
    assert "web.log" in response.headers["Content-Disposition"]


def test_log_events_emits_structured_json_lines(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_log(bench_root, "web.log", "")
    client = _client(bench_root)

    with patch(
        "admin.backend.providers.logs.LogProvider.follow_file",
        return_value=iter(["first line", "second line"]),
    ):
        response = client.get("/api/v1/logs/web.log/events")
        body = response.get_data(as_text=True)

    events = [json.loads(chunk.removeprefix("data: ")) for chunk in body.strip().split("\n\n") if chunk]
    assert events == [{"line": "first line"}, {"line": "second line"}]


def test_log_events_emits_a_structured_error_for_an_invalid_filename(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = client.get("/api/v1/logs/../secret/events")
    body = response.get_data(as_text=True)

    if response.status_code == 200:
        event = json.loads(body.strip().removeprefix("data: "))
        assert "error" in event
    else:
        assert response.status_code == 404


def test_a_quiet_log_stream_sends_a_heartbeat_comment(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    with patch("admin.backend.providers.logs.LogProvider.follow_file", return_value=iter(["first line", None])):
        body = client.get("/api/v1/logs/web.log/events").get_data(as_text=True)

    assert body == 'data: {"line": "first line"}\n\n: heartbeat\n\n'


def test_follow_file_sends_a_heartbeat_when_the_file_stays_quiet(tmp_path: Path, monkeypatch) -> None:
    import admin.backend.providers.logs as logs_module
    from admin.backend.providers.logs import LogProvider

    _make_log(tmp_path, "web.log", "")
    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr(logs_module.time, "monotonic", lambda: next(clock))

    assert next(LogProvider(tmp_path).follow_file("web.log")) is None
