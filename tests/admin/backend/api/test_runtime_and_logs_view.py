"""Tests for /api/v1/runtime and /api/v1/logs routes."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pilot.config.bench_toml_builder import BenchTomlBuilder


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from pilot.core.admin_auth import ensure_jwt_secret, issue_token

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


def _process(name="web", status="running"):
    return SimpleNamespace(
        name=name, status=status, pid=123, uptime="1h",
        cpu_percent=1.0, rss_mb=10.0, pss_mb=8.0, log_file=Path(f"{name}.log"),
    )


def test_runtime_processes_lists_processes(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    with patch(
        "admin.backend.providers.processes.ProcessProvider.get_all",
        return_value=[_process()],
    ):
        response = client.get("/api/v1/runtime/processes")

    body = response.get_json()
    assert response.status_code == 200
    assert body["processes"][0]["name"] == "web"
    assert body["production"] is False


def test_runtime_actions_require_production(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    for action in ("start", "stop", "restart"):
        response = client.post(f"/api/v1/runtime/actions/{action}")
        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "process_control_unavailable"


def test_runtime_restart_returns_the_process_list(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    conf_dir = bench_root / "config" / "supervisor"
    conf_dir.mkdir(parents=True)
    (conf_dir / "supervisord.conf").write_text("")

    status_result = Mock(returncode=0, stdout="current:web RUNNING\n")
    restart_result = Mock(returncode=0)

    with patch(
        "admin.backend.api.v1.processes.subprocess.run",
        side_effect=[status_result, restart_result],
    ), patch(
        "admin.backend.providers.processes.ProcessProvider.get_all",
        return_value=[_process()],
    ):
        response = client.post("/api/v1/runtime/actions/restart")

    body = response.get_json()
    assert response.status_code == 200
    assert body["processes"][0]["name"] == "web"


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
