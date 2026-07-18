"""Tests for the admin Flask app's bench-switcher and New Bench endpoints."""

from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import PropertyMock, patch

from pilot.config import BenchConfig


def _write_bench_toml(bench_dir: Path, name: str, **settings) -> None:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(BenchConfig.from_flat(name, settings).dumps())


def _write_raw_bench_toml(bench_dir: Path, name: str, admin_port: int) -> None:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(f'[bench]\nname = "{name}"\n\n[admin]\nport = {admin_port}\n')


def _client(bench_root: Path, password: str = "secret", **extra_settings):
    from admin.backend.app import create_app
    from admin.backend.auth import ensure_jwt_secret, issue_token

    _write_bench_toml(
        bench_root, bench_root.name, admin_enabled=True, admin_password=password, **extra_settings
    )
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


def test_api_benches_requires_auth(tmp_path: Path) -> None:
    from admin.backend.app import create_app

    bench_root = tmp_path / "benches" / "current"
    _write_bench_toml(bench_root, "current", admin_enabled=True, admin_password="secret")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()

    resp = client.get("/api/v1/benches")
    assert resp.status_code == 401
    assert resp.get_json() == {
        "error": {
            "code": "authentication_required",
            "details": {},
            "message": "Authentication is required.",
        }
    }


def test_api_benches_lists_all_benches_with_reachability(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    with _listening_socket() as live_port:
        _write_raw_bench_toml(benches_dir / "live-bench", "live-bench", admin_port=live_port)
        _write_raw_bench_toml(benches_dir / "dead-bench", "dead-bench", admin_port=1)
        resp = client.get("/api/v1/benches")

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
            f'[bench]\nname = "prod-bench"\n\n'
            f'[admin]\nport = {live_port}\ndomain = "admin-prod.example.com"\ntls = true\n\n'
            f'[production]\nenabled = true\nprocess_manager = "systemd"\n'
        )
        # https only once the cert is in place; pretend it is for this assertion.
        with patch(
            "admin.backend.providers.bench.BenchProvider.has_admin_cert",
            new_callable=PropertyMock,
            return_value=True,
        ):
            resp = client.get("/api/v1/benches")

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
            f'[bench]\nname = "prod-bench"\n\n'
            f'[admin]\nport = {live_port}\ndomain = "admin-prod.example.com"\ntls = true\n\n'
            f'[production]\nenabled = true\nprocess_manager = "systemd"\n'
        )
        with patch(
            "admin.backend.providers.bench.BenchProvider.has_admin_cert",
            new_callable=PropertyMock,
            return_value=False,
        ):
            resp = client.get("/api/v1/benches")

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
        resp = client.get("/api/v1/benches")

    entry = next(b for b in resp.get_json() if b["name"] == "with-sites")
    assert entry["site_count"] == 2


def test_api_benches_gets_one_bench(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    _write_raw_bench_toml(benches_dir / "other", "ignored-config-name", admin_port=8100)
    with (benches_dir / "other" / "bench.toml").open("a") as config_file:
        config_file.write('\n[custom_plugin]\napi_token = "must-not-leak"\n')

    resp = client.get("/api/v1/benches/other")

    assert resp.status_code == 200
    assert resp.get_json()["name"] == "other"
    assert "custom_plugin" not in resp.get_json()


def test_api_benches_get_rejects_unknown_bench(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    resp = client.get("/api/v1/benches/does-not-exist")

    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "bench_not_found"


def test_api_benches_domain_options_returns_suffixes(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    with patch(
        "pilot.core.adapters.domain_provider.DomainRouteProvider.wildcard_domains",
        return_value=["*.example.com", "*-box.example.net"],
    ):
        resp = client.get("/api/v1/benches/domain-options")

    assert resp.get_json() == {"domains": [".example.com", "-box.example.net"]}


def test_api_benches_domain_options_reports_provider_failure(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    with patch(
        "pilot.core.adapters.domain_provider.DomainRouteProvider.wildcard_domains",
        side_effect=RuntimeError("provider detail"),
    ):
        resp = client.get("/api/v1/benches/domain-options")

    assert resp.status_code == 500
    assert resp.get_json()["error"] == {
        "code": "wildcard_domains_unavailable",
        "details": {},
        "message": "Could not read wildcard domains.",
    }


def _new_payload(name: str, **overrides) -> dict:
    payload = {
        "name": name,
        "process_manager": "systemd",
        "admin_domain": f"{name}-admin.example.com",
    }
    payload.update(overrides)
    return payload


def test_api_benches_create_creates_bench(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    with (
        patch("subprocess.Popen") as mock_popen,
        patch(
            "pilot.core.adapters.domain_provider.DomainRouteProvider.wildcard_domains",
            return_value=[],
        ),
    ):
        resp = client.post("/api/v1/benches", json=_new_payload("fresh"))

    assert resp.status_code == 201
    assert resp.headers["Location"] == "/api/v1/benches/fresh"
    assert resp.get_json()["name"] == "fresh"
    toml = (benches_dir / "fresh" / "bench.toml").read_text()
    assert 'process_manager = "systemd"' in toml
    assert 'domain = "fresh-admin.example.com"' in toml
    # Stored as a preference only - not yet deployed.
    assert "enabled = false" in toml.split("[production]")[1].split("[")[0]
    mock_popen.assert_called_once()


def test_api_benches_create_routes_wizard_at_domain_when_production(tmp_path: Path) -> None:
    # A bench created from a production admin is routed to the setup wizard at its
    # own domain - not auto-provisioned to a password-protected login. Its process
    # manager and TLS choice are recorded (mirroring the parent bench) but not
    # brought up yet - that needs the venv/framework app the wizard's init step
    # installs, so WizardSetupTask finishes the job via SetupProductionCommand.
    benches_dir = tmp_path / "benches"
    current = benches_dir / "current"
    _write_bench_toml(
        current,
        "current",
        admin_enabled=True,
        admin_password="secret",
        admin_domain="current-admin.example.com",
        admin_tls=True,
    )
    from admin.backend.app import create_app

    toml = (current / "bench.toml").read_text()
    (current / "bench.toml").write_text(
        toml.replace(
            "enabled = false\nuse_companion_manager",
            'enabled = true\nprocess_manager = "systemd"\nuse_companion_manager',
        )
    )
    from admin.backend.auth import ensure_jwt_secret, issue_token

    secret = ensure_jwt_secret(current / "bench.toml")
    app = create_app(current)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))

    with (
        patch("pilot.managers.processes.systemd.SystemdProcessManager.start_admin") as mock_admin,
        patch("pilot.managers.processes.systemd.SystemdProcessManager.apply_unit_action") as mock_apply,
        patch("pilot.managers.nginx.NginxManager.generate_config") as mock_gen,
        patch("pilot.managers.nginx.NginxManager.install_config"),
        patch("pilot.managers.nginx.NginxManager.reload"),
        patch(
            "pilot.managers.nginx.NginxManager.has_admin_cert",
            new_callable=PropertyMock,
            return_value=False,
        ),
        patch("pilot.core.adapters.domain_provider.DomainRouteProvider.register") as mock_register,
        patch(
            "pilot.core.adapters.domain_provider.DomainRouteProvider.wildcard_domains",
            return_value=[],
        ),
        patch("pilot.managers.platform.has_passwordless_sudo", return_value=True),
        patch("subprocess.Popen") as mock_popen,
    ):
        resp = client.post("/api/v1/benches", json=_new_payload("fresh"))

    data = resp.get_json()
    assert resp.status_code == 201
    assert resp.headers["Location"] == "/api/v1/benches/fresh"
    assert data["wizard_at_domain"] is True
    assert data["domain"] == "fresh-admin.example.com"
    # The admin domain is registered with the provider so it resolves for the wizard.
    mock_register.assert_called_once_with("fresh-admin.example.com", "fresh-admin.example.com")
    # The new bench's OWN admin is brought up (no standalone wizard server) and
    # nginx routes its domain to it.
    mock_admin.assert_called_once()
    mock_gen.assert_called_once()
    mock_popen.assert_not_called()
    # The workload (web/worker/socketio) needs the venv and framework app that
    # only exist once the wizard's init step runs - starting it now would
    # crash-loop and permanently rate-limit the units. WizardSetupTask starts
    # it once init actually finishes, not this view.
    mock_apply.assert_not_called()
    assert data["scheme"] == "http"
    fresh_toml = (benches_dir / "fresh" / "bench.toml").read_text()
    # The sibling production bench serves TLS, so the new one inherits that choice
    # (applied once WizardSetupTask brings it up for real) instead of being
    # forced onto plain HTTP.
    assert "tls = true" in fresh_toml
    # production.enabled stays false until the wizard's init + SetupProductionCommand
    # actually finish - a half-built deployment must never look "done" to the switcher.
    assert "enabled = false" in fresh_toml.split("[production]")[1].split("[")[0]
    assert 'process_manager = "systemd"' in fresh_toml.split("[production]")[1].split("[")[0]


def test_api_benches_create_does_not_prompt_for_system_privileges(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    with (
        patch(
            "admin.backend.providers.bench.BenchProvider.is_production",
            new_callable=PropertyMock,
            return_value=True,
        ),
        patch("pilot.managers.platform.has_passwordless_sudo", return_value=False),
        patch("admin.backend.api.v1.benches.create.Bench.create_at") as create,
        patch(
            "pilot.core.adapters.domain_provider.DomainRouteProvider.wildcard_domains",
            return_value=[],
        ),
    ):
        resp = client.post("/api/v1/benches", json=_new_payload("fresh"))

    assert resp.status_code == 409
    assert resp.get_json()["error"]["code"] == "privileged_operation_unavailable"
    assert not (benches_dir / "fresh").exists()
    create.assert_not_called()


def test_api_benches_create_reports_busy_without_waiting(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    with patch(
        "admin.backend.api.v1.benches.exclusive_file_lock",
        side_effect=BlockingIOError,
    ):
        resp = client.post("/api/v1/benches", json=_new_payload("fresh"))

    assert resp.status_code == 409
    assert resp.get_json()["error"]["code"] == "bench_busy"
    assert not (benches_dir / "fresh").exists()


def test_api_benches_create_rejects_when_management_disabled(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current", admin_allow_bench_management=False)

    resp = client.post("/api/v1/benches", json=_new_payload("fresh"))

    assert resp.status_code == 403
    assert resp.get_json() == {
        "error": {
            "code": "bench_management_forbidden",
            "details": {},
            "message": "Bench management is disabled on this server.",
        }
    }
    assert not (benches_dir / "fresh").exists()


def test_api_benches_list_rejects_when_management_disabled(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current", admin_allow_bench_management=False)

    resp = client.get("/api/v1/benches")

    assert resp.status_code == 403


def test_api_benches_drop_rejects_when_management_disabled(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current", admin_allow_bench_management=False)
    _write_bench_toml(benches_dir / "other", "other", admin_port=8100)

    resp = client.delete("/api/v1/benches/other")

    assert resp.status_code == 403


def test_api_benches_create_rejects_invalid_name(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    resp = client.post(
        "/api/v1/benches",
        json={
            "name": "bad name!",
            "process_manager": "systemd",
            "admin_domain": "x-admin.example.com",
        },
    )

    assert resp.status_code == 422
    assert not (benches_dir / "bad name!").exists()


def test_api_benches_create_rejects_malformed_body(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    resp = client.post("/api/v1/benches", data="[]", content_type="application/json")

    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "malformed_request"


def test_api_benches_create_rejects_invalid_field_types(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    string_field = client.post("/api/v1/benches", json=_new_payload("fresh", db_type=True))
    tls_field = client.post("/api/v1/benches", json=_new_payload("fresh", admin_tls="yes"))

    assert string_field.status_code == 422
    assert string_field.get_json()["error"]["code"] == "invalid_bench"
    assert tls_field.status_code == 422
    assert tls_field.get_json()["error"]["code"] == "invalid_admin_tls"


def test_api_benches_create_requires_process_manager(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    resp = client.post("/api/v1/benches", json={"name": "fresh", "admin_domain": "fresh-admin.example.com"})

    assert resp.status_code == 422
    assert "process manager" in resp.get_json()["error"]["message"].lower()


def test_api_benches_create_requires_admin_domain(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    resp = client.post("/api/v1/benches", json={"name": "fresh", "process_manager": "systemd"})

    assert resp.status_code == 422
    assert "domain" in resp.get_json()["error"]["message"].lower()


def test_api_benches_create_rejects_duplicate_admin_domain(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    other = benches_dir / "other"
    (other / "sites").mkdir(parents=True, exist_ok=True)
    (other / "bench.toml").write_text(
        '[bench]\nname = "other"\n\n[admin]\ndomain = "shared-admin.example.com"\n'
    )

    resp = client.post("/api/v1/benches", json=_new_payload("fresh", admin_domain="shared-admin.example.com"))

    assert resp.status_code == 409
    assert "already used by bench 'other'" in resp.get_json()["error"]["message"]


def test_api_benches_create_rejects_duplicate_name(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    with patch("pilot.core.adapters.domain_provider.DomainRouteProvider.wildcard_domains", return_value=[]):
        resp = client.post("/api/v1/benches", json=_new_payload("current"))

    assert resp.status_code == 409
    assert resp.get_json()["error"]["code"] == "bench_already_exists"
    assert "already exists" in resp.get_json()["error"]["message"]


def test_api_benches_ready_true_when_port_live(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    with _listening_socket() as port:
        resp = client.post("/api/v1/bench-readiness-checks", json={"port": port})

    assert resp.get_json() == {"ready": True}


def test_api_benches_ready_false_when_port_not_live(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    with _listening_socket() as port:
        pass

    resp = client.post("/api/v1/bench-readiness-checks", json={"port": port})

    assert resp.get_json() == {"ready": False}


def test_api_benches_ready_false_on_invalid_port(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    resp = client.post("/api/v1/bench-readiness-checks", json={"port": "not-a-number"})

    assert resp.status_code == 422


def test_api_bench_readiness_rejects_malformed_body(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    resp = client.post(
        "/api/v1/bench-readiness-checks",
        data="[]",
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "malformed_request"


def test_api_bench_readiness_strictly_validates_selectors(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    invalid_requests = (
        ({}, "invalid_port"),
        ({"port": True}, "invalid_port"),
        ({"port": 0}, "invalid_port"),
        ({"domain": 123}, "invalid_domain"),
        ({"domain": "admin.example.com", "scheme": "ftp"}, "invalid_scheme"),
        ({"domain": "admin.example.com", "port": 8000}, "invalid_readiness_check"),
        ({"port": 8000, "scheme": "http"}, "invalid_readiness_check"),
    )
    for payload, code in invalid_requests:
        resp = client.post("/api/v1/bench-readiness-checks", json=payload)
        assert resp.status_code == 422
        assert resp.get_json()["error"]["code"] == code


@contextmanager
def _nginx_stub(health_status: int = 200):
    """Loopback nginx/admin health probe stub."""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(health_status if self.path == "/api/v1/health" else 404)
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
        resp = client.post(
            "/api/v1/bench-readiness-checks",
            json={"domain": "admin.example.com"},
        )

    assert resp.get_json() == {"ready": True}


def test_api_benches_ready_false_when_wizard_errors_at_domain(tmp_path: Path) -> None:
    current = tmp_path / "benches" / "current"
    client = _client(current)
    with _nginx_stub(health_status=502) as port:
        _set_nginx_http_port(current, port)
        resp = client.post(
            "/api/v1/bench-readiness-checks",
            json={"domain": "admin.example.com"},
        )

    assert resp.get_json() == {"ready": False}


def test_api_benches_ready_true_when_https_bench_redirects(tmp_path: Path) -> None:
    # An https bench redirects :80 -> https once the cert is in place; that
    # redirect is itself the readiness signal, so scheme=https accepts a 301.
    current = tmp_path / "benches" / "current"
    client = _client(current)
    with _nginx_stub(health_status=301) as port:
        _set_nginx_http_port(current, port)
        resp = client.post(
            "/api/v1/bench-readiness-checks",
            json={"domain": "admin.example.com", "scheme": "https"},
        )

    assert resp.get_json() == {"ready": True}


def test_api_benches_ready_false_when_http_bench_redirects(tmp_path: Path) -> None:
    # Without scheme=https a redirect is not readiness (an http bench answers 200).
    current = tmp_path / "benches" / "current"
    client = _client(current)
    with _nginx_stub(health_status=301) as port:
        _set_nginx_http_port(current, port)
        resp = client.post(
            "/api/v1/bench-readiness-checks",
            json={"domain": "admin.example.com"},
        )

    assert resp.get_json() == {"ready": False}


def test_api_benches_ready_false_when_nginx_down_at_domain(tmp_path: Path) -> None:
    current = tmp_path / "benches" / "current"
    client = _client(current)
    with _listening_socket() as port:
        pass  # nothing listening on `port` now
    _set_nginx_http_port(current, port)

    resp = client.post(
        "/api/v1/bench-readiness-checks",
        json={"domain": "admin.example.com"},
    )

    assert resp.get_json() == {"ready": False}


def _write_prod_bench_toml(bench_dir: Path, name: str) -> None:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(
        f'[bench]\nname = "{name}"\npython = "3.14"\n\n'
        f'[admin]\nport = 9999\ndomain = "{name}-admin.example.com"\n\n'
        f'[production]\nenabled = true\nprocess_manager = "systemd"\n'
    )


def test_api_benches_control_rejects_unknown_action(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    _write_prod_bench_toml(benches_dir / "prod-bench", "prod-bench")

    resp = client.post("/api/v1/benches/prod-bench/actions/wiggle")

    assert resp.status_code == 405
    assert "error" in resp.get_json()


def test_api_benches_control_rejects_unknown_bench(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    resp = client.post("/api/v1/benches/does-not-exist/actions/start")

    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_api_benches_control_rejects_dev_bench(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    _write_bench_toml(benches_dir / "dev-bench", "dev-bench")

    resp = client.post("/api/v1/benches/dev-bench/actions/start")

    assert resp.status_code == 409
    assert "production" in resp.get_json()["error"]["message"]


def test_api_benches_explicit_actions_return_updated_resource(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    _write_prod_bench_toml(benches_dir / "prod-bench", "prod-bench")

    with patch("admin.backend.api.v1.benches.Bench.run_production_action") as run_action:
        responses = {
            action: client.post(f"/api/v1/benches/prod-bench/actions/{action}")
            for action in ("start", "stop", "restart")
        }

    for _action, resp in responses.items():
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "prod-bench"
    assert [call.args[0] for call in run_action.call_args_list] == [
        "start",
        "stop",
        "restart",
    ]


def test_api_benches_control_reports_failure(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    _write_prod_bench_toml(benches_dir / "prod-bench", "prod-bench")

    with patch(
        "admin.backend.api.v1.benches.Bench.run_production_action",
        side_effect=RuntimeError("manager detail"),
    ):
        resp = client.post("/api/v1/benches/prod-bench/actions/stop")

    assert resp.status_code == 500
    assert resp.get_json()["error"]["code"] == "bench_action_failed"


def test_api_benches_drop_rejects_current_bench(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    resp = client.delete("/api/v1/benches/current")

    assert resp.status_code == 409
    assert "cannot be dropped" in resp.get_json()["error"]["message"]


def test_api_benches_drop_rejects_bench_with_sites(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    bench_dir = benches_dir / "prod-bench"
    _write_prod_bench_toml(bench_dir, "prod-bench")
    (bench_dir / "sites" / "a.localhost").mkdir(parents=True)
    (bench_dir / "sites" / "a.localhost" / "site_config.json").write_text("{}")

    with patch("subprocess.run") as mock_run:
        resp = client.delete("/api/v1/benches/prod-bench")

    assert resp.status_code == 409
    assert "site" in resp.get_json()["error"]["message"].lower()
    mock_run.assert_not_called()


def test_api_benches_drop_rejects_unknown_bench(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")

    resp = client.delete("/api/v1/benches/does-not-exist")

    assert resp.status_code == 404


def test_api_benches_drop_runs_pilot(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    _write_prod_bench_toml(benches_dir / "prod-bench", "prod-bench")

    with (
        patch("admin.backend.api.v1.benches.Bench.drop") as drop,
        patch("pilot.managers.platform.has_passwordless_sudo", return_value=True),
    ):
        resp = client.delete("/api/v1/benches/prod-bench")

    assert resp.status_code == 204
    assert resp.data == b""
    drop.assert_called_once_with()


def test_api_benches_drop_does_not_prompt_for_system_privileges(tmp_path: Path) -> None:
    benches_dir = tmp_path / "benches"
    client = _client(benches_dir / "current")
    _write_prod_bench_toml(benches_dir / "prod-bench", "prod-bench")

    with (
        patch("pilot.managers.platform.has_passwordless_sudo", return_value=False),
        patch("admin.backend.api.v1.benches.Bench.drop") as drop,
    ):
        resp = client.delete("/api/v1/benches/prod-bench")

    assert resp.status_code == 409
    assert resp.get_json()["error"]["code"] == "privileged_operation_unavailable"
    drop.assert_not_called()


def test_create_site_does_not_carry_db_type(tmp_path: Path) -> None:
    # The engine is fixed per bench, so site creation never passes a db_type.
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    with (
        patch("admin.backend.api.v1.sites.core.new_site_name_error", return_value=None),
        patch(
            "pilot.internal.tasks.runner.task_workers.wake",
            return_value=False,
        ),
    ):
        response = client.post(
            "/api/v1/sites",
            json={"name": "s.localhost", "db_type": "sqlite"},
        )

    body = response.get_json()
    assert response.status_code == 202
    assert response.headers["Location"] == f"/api/v1/tasks/{body['task_id']}"
    assert body["command"] == "new-site"
    assert "db_type" not in body["args"]
    callbacks = json.loads((bench_root / "tasks" / body["task_id"] / "callbacks.json").read_text())
    assert (
        callbacks["on_cancel"]
        == callbacks["on_failure"]
        == {
            "operation": "remove-failed-site",
            "args": {"site": "s.localhost"},
        }
    )


def test_reinstall_site_generates_new_admin_password(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    site_dir = bench_root / "sites" / "s.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{}")

    with patch(
        "pilot.internal.tasks.runner.task_workers.wake",
        return_value=False,
    ):
        response = client.post("/api/v1/sites/s.localhost/actions/reinstall", json={})

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "reinstall-site"
    assert body["args"] == {"site": "s.localhost", "admin_password": "[redacted]"}
    secrets_payload = json.loads((bench_root / "tasks" / body["task_id"] / "secrets.json").read_text())
    assert secrets_payload["admin_password"]
    assert secrets_payload["admin_password"] != "admin"


def test_reinstall_site_submits_new_admin_password_as_secret(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    site_dir = bench_root / "sites" / "s.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{}")

    with patch(
        "pilot.internal.tasks.runner.task_workers.wake",
        return_value=False,
    ):
        response = client.post(
            "/api/v1/sites/s.localhost/actions/reinstall",
            json={"admin_password": "new-secret"},
        )

    body = response.get_json()
    assert response.status_code == 202
    assert body["args"]["admin_password"] == "[redacted]"
    assert "new-secret" not in json.dumps(body)
    assert json.loads((bench_root / "tasks" / body["task_id"] / "secrets.json").read_text()) == {
        "admin_password": "new-secret"
    }


def test_site_actions_return_canonical_task_resources(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    cases = [
        ("reinstall", "reinstall-site", {}),
        ("clear-cache", "clear-cache", {}),
        ("migrate", "migrate", {}),
        ("enable-tls", "setup-letsencrypt", {"email": "ops@example.com"}),
    ]

    with patch(
        "pilot.internal.tasks.runner.task_workers.wake",
        return_value=False,
    ):
        for index, (action, command, payload) in enumerate(cases):
            site = f"s{index}.localhost"
            site_dir = bench_root / "sites" / site
            site_dir.mkdir(parents=True)
            (site_dir / "site_config.json").write_text("{}")

            response = client.post(
                f"/api/v1/sites/{site}/actions/{action}",
                json=payload,
            )

            body = response.get_json()
            assert response.status_code == 202
            assert response.headers["Location"] == f"/api/v1/tasks/{body['task_id']}"
            assert body["command"] == command
            assert body["args"]["site"] == site


def test_site_action_idempotency_replays_same_task(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    site_dir = bench_root / "sites" / "s.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{}")

    with patch(
        "pilot.internal.tasks.runner.task_workers.wake",
        return_value=False,
    ):
        first = client.post(
            "/api/v1/sites/s.localhost/actions/clear-cache",
            headers={"Idempotency-Key": "clear-s"},
        )
        replay = client.post(
            "/api/v1/sites/s.localhost/actions/clear-cache",
            headers={"Idempotency-Key": "clear-s"},
        )

    assert first.status_code == replay.status_code == 202
    assert first.get_json()["task_id"] == replay.get_json()["task_id"]


def test_site_actions_reject_missing_and_symlinked_sites(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    outside = tmp_path / "outside-site"
    outside.mkdir()
    (outside / "site_config.json").write_text("{}")
    sites_dir = bench_root / "sites"
    sites_dir.mkdir()
    (sites_dir / "linked.localhost").symlink_to(outside, target_is_directory=True)

    missing = client.post("/api/v1/sites/missing.localhost/actions/migrate")
    linked = client.post("/api/v1/sites/linked.localhost/actions/clear-cache")

    assert missing.status_code == linked.status_code == 404
    assert not (bench_root / "tasks").exists()
