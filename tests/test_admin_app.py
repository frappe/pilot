"""Tests for the admin Flask app's bench-switcher and New Bench endpoints."""

from __future__ import annotations

import socket
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from bench_cli.config.bench_toml_builder import BenchTomlBuilder


def _write_bench_toml(bench_dir: Path, name: str, **settings) -> None:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(BenchTomlBuilder(name, settings).render())


def _write_raw_bench_toml(bench_dir: Path, name: str, admin_port: int) -> None:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(f'[bench]\nname = "{name}"\n\n[admin]\nport = {admin_port}\n')


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app

    _write_bench_toml(bench_root, bench_root.name, admin_enabled=True, admin_password=password)
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
    return client


@contextmanager
def _listening_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


# ── GET /api/benches/ ────────────────────────────────────────────────────────


def test_api_benches_requires_auth(tmp_path: Path) -> None:
    from admin.backend.app import create_app

    bench_root = tmp_path / "benches" / "current"
    _write_bench_toml(bench_root, "current", admin_enabled=True, admin_password="secret")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()

    resp = client.get("/api/benches/")
    assert resp.status_code == 401


def test_api_benches_lists_only_running_benches(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    with _listening_socket() as live_port:
        _write_raw_bench_toml(benches_dir / "live-bench", "live-bench", admin_port=live_port)
        _write_raw_bench_toml(benches_dir / "dead-bench", "dead-bench", admin_port=1)
        resp = client.get("/api/benches/")

    names = [b["name"] for b in resp.get_json()]
    assert "live-bench" in names
    assert "dead-bench" not in names


# ── POST /api/benches/new ────────────────────────────────────────────────────


def test_api_benches_new_creates_bench(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    with patch("subprocess.Popen") as mock_popen:
        resp = client.post("/api/benches/new", json={"name": "fresh"})

    assert resp.get_json()["name"] == "fresh"
    assert (benches_dir / "fresh" / "bench.toml").exists()
    mock_popen.assert_called_once()


def test_api_benches_new_rejects_invalid_name(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    resp = client.post("/api/benches/new", json={"name": "bad name!"})

    assert resp.status_code == 400
    assert not (benches_dir / "bad name!").exists()


def test_api_benches_new_rejects_duplicate_name(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    resp = client.post("/api/benches/new", json={"name": "current"})

    assert resp.status_code == 400
    assert "already exists" in resp.get_json()["error"]


# ── GET /api/benches/ready ───────────────────────────────────────────────────


def test_api_benches_ready_true_when_port_live(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    with _listening_socket() as port:
        resp = client.get(f"/api/benches/ready?port={port}")

    assert resp.get_json() == {"ready": True}


def test_api_benches_ready_false_when_port_not_live(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    with _listening_socket() as port:
        pass

    resp = client.get(f"/api/benches/ready?port={port}")

    assert resp.get_json() == {"ready": False}


def test_api_benches_ready_false_on_invalid_port(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    resp = client.get("/api/benches/ready?port=not-a-number")

    assert resp.status_code == 400
