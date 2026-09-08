"""The admin Processes page's API: grouped bench/app processes, blocked-app state."""

from __future__ import annotations

from pathlib import Path

from pilot.config import BenchConfig
from pilot.core.bench import Bench

_GOOD = '[tool.pilot.background_processes.stalwart]\ncmd = ["/usr/bin/stalwart-mail"]\n'


def _write_bench_toml(bench_dir: Path, name: str, **settings) -> None:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(BenchConfig.from_flat(name, settings).dumps())


def _make_app(bench_dir: Path, name: str, pyproject: str) -> None:
    app_dir = bench_dir / "apps" / name
    (app_dir / ".git").mkdir(parents=True)
    (app_dir / "pyproject.toml").write_text(pyproject)


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session

    _write_bench_toml(bench_root, bench_root.name, admin_enabled=True, admin_password=password)
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", Session(Bench(bench_root)).issue_session_token()[0])
    return client


def test_list_processes_groups_bench_and_app_processes(tmp_path: Path) -> None:
    bench_root = tmp_path / "current"
    client = _client(bench_root)
    _make_app(bench_root, "mail", _GOOD)

    resp = client.get("/api/v1/processes")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["blocked"] is None

    sources = {group["source"] for group in data["groups"]}
    assert "bench" in sources
    assert "mail" in sources

    mail_group = next(g for g in data["groups"] if g["source"] == "mail")
    stalwart = next(p for p in mail_group["processes"] if p["name"] == "mail-stalwart")
    assert stalwart["declaration"]["cmd"] == "/usr/bin/stalwart-mail"
    assert stalwart["state"] == "stopped"  # nothing is actually running in this test


def test_list_processes_reports_blocked_app_instead_of_500(tmp_path: Path) -> None:
    bench_root = tmp_path / "current"
    client = _client(bench_root)
    _make_app(bench_root, "broken", "[tool.pilot\n")  # malformed TOML

    resp = client.get("/api/v1/processes")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["groups"] == []
    assert data["blocked"]["app"] == "broken"


def test_restart_process_without_a_process_manager_is_a_clean_409(tmp_path: Path) -> None:
    bench_root = tmp_path / "current"
    client = _client(bench_root)
    _make_app(bench_root, "mail", _GOOD)

    resp = client.post("/api/v1/processes/mail-stalwart/actions/restart")
    assert resp.status_code == 409
    assert resp.get_json()["error"]["code"] == "process_control_unavailable"
