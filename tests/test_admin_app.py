"""Tests for the admin Flask app's bench-switcher and New Bench endpoints."""

from __future__ import annotations

import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from pilot.config.bench_toml_builder import BenchTomlBuilder


def _write_bench_toml(bench_dir: Path, name: str, **settings) -> None:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(BenchTomlBuilder(name, settings).render())


def _write_raw_bench_toml(bench_dir: Path, name: str, admin_port: int) -> None:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(f'[bench]\nname = "{name}"\n\n[admin]\nport = {admin_port}\n')


def _client(bench_root: Path, password: str = "secret", **settings):
    from admin.backend.app import create_app
    from pilot.commands.generate_session import ensure_jwt_secret, issue_token

    _write_bench_toml(bench_root, bench_root.name, admin_enabled=True, admin_password=password, **settings)
    secret = ensure_jwt_secret(bench_root / "bench.toml")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))
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


def test_api_benches_lists_all_benches_with_reachability(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    with _listening_socket() as live_port:
        _write_raw_bench_toml(benches_dir / "live-bench", "live-bench", admin_port=live_port)
        _write_raw_bench_toml(benches_dir / "dead-bench", "dead-bench", admin_port=1)
        resp = client.get("/api/benches/")

    entries = {b["name"]: b for b in resp.get_json()}
    # Stopped benches are listed too, flagged unreachable rather than hidden.
    assert entries["live-bench"]["reachable"] is True
    assert entries["dead-bench"]["reachable"] is False


def test_api_benches_includes_production_metadata(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    with _listening_socket() as live_port:
        prod_dir = benches_dir / "prod-bench"
        prod_dir.mkdir(parents=True, exist_ok=True)
        (prod_dir / "bench.toml").write_text(
            f'[bench]\nname = "prod-bench"\n\n[admin]\nport = {live_port}\ndomain = "admin-prod.example.com"\ntls = true\n\n[production]\nenabled = true\nprocess_manager = "systemd"\n'
        )
        # https only once the cert is in place; pretend it is for this assertion.
        with patch("admin.backend.views.benches.admin_cert_exists", return_value=True):
            resp = client.get("/api/benches/")

    entry = next(b for b in resp.get_json() if b["name"] == "prod-bench")
    assert entry["production"] is True
    assert entry["process_manager"] == "systemd"
    assert entry["admin_url"] == "https://admin-prod.example.com"


def test_api_benches_admin_url_is_http_until_cert_exists(tmp_path: Path) -> None:
    # A tls bench whose cert isn't issued yet is served over plain http, so the
    # switcher must open it over http (not the current https page's scheme).
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    with _listening_socket() as live_port:
        prod_dir = benches_dir / "prod-bench"
        prod_dir.mkdir(parents=True, exist_ok=True)
        (prod_dir / "bench.toml").write_text(
            f'[bench]\nname = "prod-bench"\n\n[admin]\nport = {live_port}\ndomain = "admin-prod.example.com"\ntls = true\n\n[production]\nenabled = true\nprocess_manager = "systemd"\n'
        )
        with patch("admin.backend.views.benches.admin_cert_exists", return_value=False):
            resp = client.get("/api/benches/")

    entry = next(b for b in resp.get_json() if b["name"] == "prod-bench")
    assert entry["admin_url"] == "http://admin-prod.example.com"


def test_api_benches_includes_site_count(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    with _listening_socket() as live_port:
        bench_dir = benches_dir / "with-sites"
        _write_raw_bench_toml(bench_dir, "with-sites", admin_port=live_port)
        # Two real sites plus a non-site dir (assets) that must not be counted.
        for site in ("a.localhost", "b.localhost"):
            (bench_dir / "sites" / site).mkdir(parents=True)
            (bench_dir / "sites" / site / "site_config.json").write_text("{}")
        (bench_dir / "sites" / "assets").mkdir(parents=True)
        resp = client.get("/api/benches/")

    entry = next(b for b in resp.get_json() if b["name"] == "with-sites")
    assert entry["site_count"] == 2


# ── POST /api/benches/new ────────────────────────────────────────────────────


def _new_payload(name: str, **overrides) -> dict:
    payload = {"name": name, "process_manager": "systemd", "admin_domain": f"{name}-admin.example.com"}
    payload.update(overrides)
    return payload


def test_api_benches_new_creates_bench(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    with patch("subprocess.Popen") as mock_popen, patch("pilot.core.domain_controller.DomainRouteProvider.wildcard_domains", return_value=[]):
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
    _write_bench_toml(current, "current", admin_enabled=True, admin_password="secret", admin_domain="current-admin.example.com", admin_tls=True)
    from admin.backend.app import create_app

    toml = (current / "bench.toml").read_text()
    (current / "bench.toml").write_text(toml.replace("enabled = false\nuse_companion_manager", 'enabled = true\nprocess_manager = "systemd"\nuse_companion_manager'))
    from pilot.commands.generate_session import ensure_jwt_secret, issue_token

    secret = ensure_jwt_secret(current / "bench.toml")
    app = create_app(current)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))

    with (
        patch("pilot.managers.process_managers.systemd.SystemdProcessManager.start_admin") as mock_admin,
        patch("pilot.managers.nginx_manager.NginxManager.generate_config") as mock_gen,
        patch("pilot.managers.nginx_manager.NginxManager.install_config"),
        patch("pilot.managers.nginx_manager.NginxManager.reload"),
        patch("pilot.core.domain_controller.DomainRouteProvider.register") as mock_register,
        patch("pilot.core.domain_controller.DomainRouteProvider.wildcard_domains", return_value=[]),
        patch("subprocess.Popen") as mock_popen,
    ):
        resp = client.post("/api/benches/new", json=_new_payload("fresh"))

    data = resp.get_json()
    assert data["wizard_at_domain"] is True
    assert data["domain"] == "fresh-admin.example.com"
    # The admin domain is registered with the provider so it resolves for the wizard.
    mock_register.assert_called_once_with("fresh-admin.example.com", "fresh-admin.example.com")
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
    (other / "bench.toml").write_text('[bench]\nname = "other"\n\n[admin]\ndomain = "shared-admin.example.com"\n')

    resp = client.post("/api/benches/new", json=_new_payload("fresh", admin_domain="shared-admin.example.com"))

    assert resp.status_code == 400
    assert "already used by bench 'other'" in resp.get_json()["error"]


def test_api_benches_new_rejects_duplicate_name(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    with patch("pilot.core.domain_controller.DomainRouteProvider.wildcard_domains", return_value=[]):
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


@contextmanager
def _nginx_stub(ping_status: int = 200):
    """A loopback HTTP server standing in for nginx+admin: answers /api/ping with
    the given status (any Host), so the domain readiness probe can be exercised."""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(ping_status if self.path == "/api/ping" else 404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()


def _set_nginx_http_port(bench_dir: Path, port: int) -> None:
    path = bench_dir / "bench.toml"
    path.write_text(path.read_text() + f"\n[nginx]\nhttp_port = {port}\n")


def test_api_benches_ready_true_when_wizard_answers_at_domain(tmp_path: Path) -> None:
    current = tmp_path / "benches" / "current"
    client = _client(current)
    with _nginx_stub() as port:
        _set_nginx_http_port(current, port)
        resp = client.get("/api/benches/ready?domain=admin.example.com")

    assert resp.get_json() == {"ready": True}


def test_api_benches_ready_false_when_wizard_errors_at_domain(tmp_path: Path) -> None:
    current = tmp_path / "benches" / "current"
    client = _client(current)
    with _nginx_stub(ping_status=502) as port:
        _set_nginx_http_port(current, port)
        resp = client.get("/api/benches/ready?domain=admin.example.com")

    assert resp.get_json() == {"ready": False}


def test_api_benches_ready_true_when_https_bench_redirects(tmp_path: Path) -> None:
    # An https bench redirects :80 -> https once the cert is in place; that
    # redirect is itself the readiness signal, so scheme=https accepts a 301.
    current = tmp_path / "benches" / "current"
    client = _client(current)
    with _nginx_stub(ping_status=301) as port:
        _set_nginx_http_port(current, port)
        resp = client.get("/api/benches/ready?domain=admin.example.com&scheme=https")

    assert resp.get_json() == {"ready": True}


def test_api_benches_ready_false_when_http_bench_redirects(tmp_path: Path) -> None:
    # Without scheme=https a redirect is not readiness (an http bench answers 200).
    current = tmp_path / "benches" / "current"
    client = _client(current)
    with _nginx_stub(ping_status=301) as port:
        _set_nginx_http_port(current, port)
        resp = client.get("/api/benches/ready?domain=admin.example.com")

    assert resp.get_json() == {"ready": False}


def test_api_benches_ready_false_when_nginx_down_at_domain(tmp_path: Path) -> None:
    current = tmp_path / "benches" / "current"
    client = _client(current)
    with _listening_socket() as port:
        pass  # nothing listening on `port` now
    _set_nginx_http_port(current, port)

    resp = client.get("/api/benches/ready?domain=admin.example.com")

    assert resp.get_json() == {"ready": False}


# ── POST /api/benches/<name>/actions/<action_name> ────────────────────────────


def _write_prod_bench_toml(bench_dir: Path, name: str) -> None:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(
        f'[bench]\nname = "{name}"\n\n[admin]\nport = 9999\n\n'
        f'[production]\nenabled = true\nprocess_manager = "systemd"\n'
    )


def test_api_benches_control_rejects_unknown_action(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    _write_prod_bench_toml(benches_dir / "prod-bench", "prod-bench")

    resp = client.post("/api/benches/prod-bench/actions/wiggle")

    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_api_benches_control_rejects_unknown_bench(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    resp = client.post("/api/benches/does-not-exist/actions/start")

    assert resp.status_code == 404
    assert resp.get_json()["ok"] is False


def test_api_benches_control_rejects_dev_bench(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    _write_bench_toml(benches_dir / "dev-bench", "dev-bench")

    resp = client.post("/api/benches/dev-bench/actions/start")

    assert resp.status_code == 400
    assert "production" in resp.get_json()["error"]


def test_api_benches_control_runs_pilot_and_reports_success(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    _write_prod_bench_toml(benches_dir / "prod-bench", "prod-bench")

    with patch("admin.backend.views.benches.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        resp = client.post("/api/benches/prod-bench/actions/restart")

    assert resp.get_json() == {"ok": True}
    argv = mock_run.call_args.args[0]
    assert argv[-3:] == ["-b", "prod-bench", "restart"]


def test_api_benches_control_reports_failure(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    _write_prod_bench_toml(benches_dir / "prod-bench", "prod-bench")

    with patch("admin.backend.views.benches.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "boom"
        resp = client.post("/api/benches/prod-bench/actions/stop")

    assert resp.status_code == 500
    assert resp.get_json() == {"ok": False, "error": "boom"}


# ── DELETE /api/benches/<name> (drop) ────────────────────────────────────────


def test_api_benches_drop_rejects_current_bench(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    resp = client.delete("/api/benches/current")

    assert resp.status_code == 400
    assert "currently using" in resp.get_json()["error"]


def test_api_benches_drop_rejects_bench_with_sites(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    bench_dir = benches_dir / "prod-bench"
    _write_prod_bench_toml(bench_dir, "prod-bench")
    (bench_dir / "sites" / "a.localhost").mkdir(parents=True)
    (bench_dir / "sites" / "a.localhost" / "site_config.json").write_text("{}")

    with patch("admin.backend.views.benches.subprocess.run") as mock_run:
        resp = client.delete("/api/benches/prod-bench")

    assert resp.status_code == 400
    assert "site" in resp.get_json()["error"].lower()
    mock_run.assert_not_called()


def test_api_benches_drop_rejects_unknown_bench(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    resp = client.delete("/api/benches/does-not-exist")

    assert resp.status_code == 404


def test_api_benches_drop_runs_pilot(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    _write_prod_bench_toml(benches_dir / "prod-bench", "prod-bench")

    with patch("admin.backend.views.benches.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        resp = client.delete("/api/benches/prod-bench")

    assert resp.get_json() == {"ok": True}
    argv = mock_run.call_args.args[0]
    assert argv[-4:] == ["--yes", "-b", "prod-bench", "drop"]


# ── POST /api/sites/create — engine is bench-level, not per-site ──────────────


def test_create_site_does_not_carry_db_type(tmp_path: Path) -> None:
    # The engine is fixed per bench, so site creation never passes a db_type.
    client = _client(tmp_path / "benches" / "current")
    captured: dict = {}

    def fake_run(self, command, args, callbacks=None):
        captured["args"] = args
        return "task_123"

    with patch("admin.backend.views.sites._new_site_name_error", return_value=None), patch("admin.backend.views.sites.TaskRunner.run", new=fake_run):
        resp = client.post("/api/sites/create", json={"name": "s.localhost"})

    assert resp.get_json()["ok"] is True
    assert "db_type" not in captured["args"]


# ── upload a MariaDB backup onto a PostgreSQL bench — prompt before converting ──


def _upload_mariadb_dump(client, name: str = "new.localhost", convert: bool = False):
    import gzip
    import io

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(b"-- MariaDB dump 10.19\nCREATE TABLE `tabUser` (\n")
    buf.seek(0)
    data = {"name": name, "admin_password": "admin", "db_file": (buf, "backup.sql.gz")}
    if convert:
        data["convert"] = "true"
    return client.post("/api/sites/create-from-upload", data=data, content_type="multipart/form-data")


def test_upload_mariadb_to_postgres_prompts_for_conversion(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current", db_type="postgres")
    with (
        patch("admin.backend.views.sites._new_site_name_error", return_value=None),
        patch("admin.backend.views.sites.is_linux", return_value=True),
        patch("admin.backend.views.sites.is_x86_64", return_value=True),
        patch("admin.backend.views.sites.TaskRunner.run") as run,
    ):
        resp = _upload_mariadb_dump(client)

    payload = resp.get_json()
    assert payload["needs_conversion_confirm"] is True
    assert "pgloader" in payload["message"]
    assert payload["db_file"]
    run.assert_not_called()  # restore must wait for the user to confirm


def test_upload_mariadb_to_postgres_convert_confirmed_forces(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current", db_type="postgres")
    with (
        patch("admin.backend.views.sites._new_site_name_error", return_value=None),
        patch("admin.backend.views.sites.is_linux", return_value=True),
        patch("admin.backend.views.sites.is_x86_64", return_value=True),
        patch("admin.backend.views.sites.TaskRunner.run", return_value="tid") as run,
    ):
        resp = _upload_mariadb_dump(client, convert=True)

    assert resp.get_json()["ok"] is True
    # convert must reach the task so _build_argv adds --force (which drives the conversion)
    assert run.call_args.args[1]["convert"] is True


def test_upload_mariadb_to_postgres_blocked_off_x86_linux(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current", db_type="postgres")
    with (
        patch("admin.backend.views.sites._new_site_name_error", return_value=None),
        patch("admin.backend.views.sites.is_linux", return_value=True),
        patch("admin.backend.views.sites.is_x86_64", return_value=False),
        patch("admin.backend.views.sites.TaskRunner.run") as run,
    ):
        resp = _upload_mariadb_dump(client)

    payload = resp.get_json()
    assert payload["ok"] is False
    assert "needs_conversion_confirm" not in payload
    assert "x86_64" in payload["error"]
    run.assert_not_called()
    uploads = tmp_path / "benches" / "current" / "tmp" / "uploads"
    assert list(uploads.iterdir()) == []  # dead-end upload is cleaned up, not leaked


def test_discard_upload_removes_staged_dir(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    upload_dir = bench_root / "tmp" / "uploads" / "tok123"
    upload_dir.mkdir(parents=True)
    db_file = upload_dir / "backup.sql.gz"
    db_file.write_text("x")

    resp = client.post("/api/sites/discard-upload", json={"db_file": str(db_file)})

    assert resp.get_json()["ok"] is True
    assert not upload_dir.exists()


def test_discard_upload_ignores_paths_outside_uploads(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    outside = bench_root / "important"
    outside.mkdir(parents=True)
    keep = outside / "f.txt"
    keep.write_text("keep")

    resp = client.post("/api/sites/discard-upload", json={"db_file": str(keep)})

    assert resp.get_json()["ok"] is True
    assert keep.exists()  # not under tmp/uploads — left untouched
