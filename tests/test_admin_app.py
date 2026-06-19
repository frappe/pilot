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


def test_api_benches_includes_production_metadata(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    with _listening_socket() as live_port:
        prod_dir = benches_dir / "prod-bench"
        prod_dir.mkdir(parents=True, exist_ok=True)
        (prod_dir / "bench.toml").write_text(
            f'[bench]\nname = "prod-bench"\n\n'
            f'[admin]\nport = {live_port}\ndomain = "admin-prod.example.com"\ntls = true\n\n'
            f'[production]\nenabled = true\nprocess_manager = "systemd"\n'
        )
        resp = client.get("/api/benches/")

    entry = next(b for b in resp.get_json() if b["name"] == "prod-bench")
    assert entry["production"] is True
    assert entry["process_manager"] == "systemd"
    assert entry["admin_url"] == "https://admin-prod.example.com"


# ── POST /api/benches/new ────────────────────────────────────────────────────


def _new_payload(name: str, **overrides) -> dict:
    payload = {"name": name, "process_manager": "systemd", "admin_domain": f"{name}-admin.example.com"}
    payload.update(overrides)
    return payload


def test_api_benches_new_creates_bench(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    with patch("subprocess.Popen") as mock_popen:
        resp = client.post("/api/benches/new", json=_new_payload("fresh"))

    assert resp.get_json()["name"] == "fresh"
    toml = (benches_dir / "fresh" / "bench.toml").read_text()
    assert 'process_manager = "systemd"' in toml
    assert 'domain = "fresh-admin.example.com"' in toml
    # Stored as a preference only — not yet deployed.
    assert "enabled = false" in toml.split("[production]")[1].split("[")[0]
    mock_popen.assert_called_once()


def test_api_benches_new_routes_wizard_at_domain_when_production(tmp_path: Path) -> None:
    # A bench created from a production admin is routed to the setup wizard at its
    # own domain (HTTP) — not auto-provisioned to a password-protected login.
    benches_dir = tmp_path / "benches"
    current = benches_dir / "current"
    _write_bench_toml(current, "current", admin_enabled=True, admin_password="secret",
                      admin_domain="current-admin.example.com", admin_tls=True)
    from admin.backend.app import create_app
    toml = (current / "bench.toml").read_text()
    (current / "bench.toml").write_text(
        toml.replace("enabled = false\nuse_companion_manager",
                     'enabled = true\nprocess_manager = "systemd"\nuse_companion_manager')
    )
    app = create_app(current)
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True

    with patch("bench_cli.managers.systemd_process_manager.SystemdProcessManager.setup_admin") as mock_admin, \
         patch("bench_cli.managers.nginx_manager.NginxManager.generate_config") as mock_gen, \
         patch("bench_cli.managers.nginx_manager.NginxManager.install_config"), \
         patch("bench_cli.managers.nginx_manager.NginxManager.reload"), \
         patch("subprocess.Popen") as mock_popen:
        resp = client.post("/api/benches/new", json=_new_payload("fresh"))

    data = resp.get_json()
    assert data["wizard_at_domain"] is True
    assert data["domain"] == "fresh-admin.example.com"
    # The new bench's OWN admin is brought up (no standalone wizard server) and
    # nginx routes its domain to it.
    mock_admin.assert_called_once()
    mock_gen.assert_called_once()
    mock_popen.assert_not_called()
    fresh_toml = (benches_dir / "fresh" / "bench.toml").read_text()
    # New benches from the UI default to plain HTTP (TLS is opt-in afterwards).
    assert "tls = false" in fresh_toml
    # Its admin now runs under the chosen manager, so it's recorded as production
    # (else `bench status`/`stop` would treat it as a foreground dev bench).
    assert "enabled = true" in fresh_toml.split("[production]")[1].split("[")[0]


def test_api_benches_new_rejects_invalid_name(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    resp = client.post("/api/benches/new", json={"name": "bad name!", "process_manager": "systemd", "admin_domain": "x-admin.example.com"})

    assert resp.status_code == 400
    assert not (benches_dir / "bad name!").exists()


def test_api_benches_new_requires_process_manager(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    resp = client.post("/api/benches/new", json={"name": "fresh", "admin_domain": "fresh-admin.example.com"})

    assert resp.status_code == 400
    assert "process manager" in resp.get_json()["error"].lower()


def test_api_benches_new_requires_admin_domain(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    resp = client.post("/api/benches/new", json={"name": "fresh", "process_manager": "systemd"})

    assert resp.status_code == 400
    assert "domain" in resp.get_json()["error"].lower()


def test_api_benches_new_rejects_duplicate_admin_domain(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    other = benches_dir / "other"
    (other / "sites").mkdir(parents=True, exist_ok=True)
    (other / "bench.toml").write_text(
        '[bench]\nname = "other"\n\n[admin]\ndomain = "shared-admin.example.com"\n'
    )

    resp = client.post("/api/benches/new", json=_new_payload("fresh", admin_domain="shared-admin.example.com"))

    assert resp.status_code == 400
    assert "already used by bench 'other'" in resp.get_json()["error"]


def test_api_benches_new_rejects_duplicate_name(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    resp = client.post("/api/benches/new", json=_new_payload("current"))

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
