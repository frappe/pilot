from __future__ import annotations

import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from pilot.commands.generate_session import (
    decode_token,
    has_scope,
    issue_login_token,
    issue_site_token,
    issue_token,
    verify_token,
)
from pilot.config.bench_config import BenchConfig
from pilot.config.bench_toml_builder import BenchTomlBuilder
from pilot.core.bench import Bench
from pilot.exceptions import BenchError


# ── JWT module ────────────────────────────────────────────────────────────────


def test_round_trip_is_valid() -> None:
    assert verify_token(issue_token("k3y"), "k3y")


def test_wrong_secret_rejected() -> None:
    assert not verify_token(issue_token("k3y"), "other")


def test_tampered_token_rejected() -> None:
    assert not verify_token(issue_token("k3y") + "x", "k3y")


def test_expired_token_rejected() -> None:
    assert not verify_token(issue_token("k3y", ttl=10, issued_at=time.time() - 100), "k3y")


def test_empty_inputs_rejected() -> None:
    assert not verify_token("", "k3y")
    assert not verify_token(issue_token("k3y"), "")


def test_issue_requires_secret() -> None:
    with pytest.raises(ValueError):
        issue_token("")


def test_login_token_carries_jti() -> None:
    assert decode_token(issue_login_token("k3y"), "k3y").get("jti")


# ── CLI command ───────────────────────────────────────────────────────────────


def _bench(tmp_path: Path, password: str = "secret") -> Bench:
    toml_path = tmp_path / "bench.toml"
    toml_path.write_text(BenchTomlBuilder(tmp_path.name, {"admin_password": password}).render())
    return _load_bench(tmp_path)


def _load_bench(tmp_path: Path) -> Bench:
    return Bench(BenchConfig.from_file(tmp_path / "bench.toml"), tmp_path)


def test_command_issues_verifiable_token_and_persists_secret(tmp_path, capsys) -> None:
    from pilot.commands.generate_session import GenerateSessionCommand

    GenerateSessionCommand(_bench(tmp_path)).run()
    token = capsys.readouterr().out.strip()
    secret = tomllib.loads((tmp_path / "bench.toml").read_text())["admin"]["jwt_secret"]
    assert secret and verify_token(token, secret)


def test_command_reuses_existing_secret(tmp_path) -> None:
    from pilot.commands.generate_session import GenerateSessionCommand

    GenerateSessionCommand(_bench(tmp_path)).run()
    first = tomllib.loads((tmp_path / "bench.toml").read_text())["admin"]["jwt_secret"]
    GenerateSessionCommand(_load_bench(tmp_path)).run()
    assert tomllib.loads((tmp_path / "bench.toml").read_text())["admin"]["jwt_secret"] == first


def test_command_full_path_builds_url(tmp_path, capsys) -> None:
    from pilot.commands.generate_session import GenerateSessionCommand

    GenerateSessionCommand(_bench(tmp_path), full_path=True).run()
    assert capsys.readouterr().out.strip().startswith("http://")


def test_command_requires_password(tmp_path) -> None:
    from pilot.commands.generate_session import GenerateSessionCommand

    with pytest.raises(BenchError):
        GenerateSessionCommand(_bench(tmp_path, password="")).run()


# ── backend cookie auth ───────────────────────────────────────────────────────


def _initialized_bench(bench_dir: Path, password: str, jwt_secret: str) -> None:
    from pilot.config.toml_store import BenchTomlStore

    bench_dir.mkdir(parents=True, exist_ok=True)
    toml_path = bench_dir / "bench.toml"
    toml_path.write_text(
        BenchTomlBuilder(
            bench_dir.name, {"admin_enabled": True, "admin_password": password}
        ).render()
    )
    config = BenchConfig.from_file(toml_path)
    config.admin.jwt_secret = jwt_secret
    BenchTomlStore(toml_path).write(config)
    python = bench_dir / "env" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.touch()


def _client(tmp_path: Path, jwt_secret: str = "k3y"):
    from admin.backend.app import create_app

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", jwt_secret)
    app = create_app(bench_root)
    app.config["TESTING"] = True
    return app.test_client()


def test_valid_jwt_cookie_authenticates(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", issue_token("k3y"))
    assert client.get("/api/v1/session").get_json() == {
        "authenticated": True,
        "scope": "bench",
    }
    assert client.get("/api/v1/benches/").status_code != 401


def test_invalid_jwt_cookie_stays_unauthenticated(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", issue_token("wrong-secret"))
    assert client.get("/api/v1/session").get_json() == {"authenticated": False}
    assert client.get("/api/v1/benches/").status_code == 401


def test_bootstrap_does_not_report_session_state(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", issue_token("k3y"))

    body = client.get("/api/v1/bootstrap").get_json()

    assert body["mode"] == "admin"
    assert "authenticated" not in body


def test_fresh_bench_bootstrap_and_session_are_explicit(tmp_path: Path) -> None:
    from admin.backend.app import create_app

    client = create_app(tmp_path).test_client()

    assert client.get("/api/v1/bootstrap").get_json() == {
        "enabled": True,
        "mode": "setup",
        "name": tmp_path.name,
    }
    assert client.get("/api/v1/session").get_json() == {"authenticated": False}


def test_delete_session_clears_cookie(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", issue_token("k3y"))

    response = client.delete("/api/v1/session")

    assert response.status_code == 204
    assert response.data == b""
    assert client.get("/api/v1/session").get_json() == {"authenticated": False}


def test_bootstrap_reports_bench_db_type(tmp_path: Path) -> None:
    # The engine is a bench-wide property; the admin reads it from bootstrap to
    # show one bench-level badge instead of a per-site one.
    client = _client(tmp_path)
    assert client.get("/api/v1/bootstrap").get_json()["db_type"] == "mariadb"


def test_bootstrap_reports_sanitized_task_activity(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/api/v1/bootstrap").get_json()

    assert body["task_worker"] == {
        "active": False,
        "desired": "running",
        "status": "not-started",
        "uncertain": False,
    }
    assert "current_task_id" not in body["task_worker"]


def test_bootstrap_reports_postgres_engine(tmp_path: Path) -> None:
    from admin.backend.app import create_app
    from pilot.config.toml_store import BenchTomlStore

    bench_root = tmp_path / "benches" / "pg"
    _initialized_bench(bench_root, "secret", "k3y")
    toml_path = bench_root / "bench.toml"
    config = BenchConfig.from_file(toml_path)
    config.db_type = "postgres"
    BenchTomlStore(toml_path).write(config)

    app = create_app(bench_root)
    app.config["TESTING"] = True
    assert app.test_client().get("/api/v1/bootstrap").get_json()["db_type"] == "postgres"


def test_bootstrap_reports_allow_bench_management_default_true(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/v1/bootstrap").get_json()["allow_bench_management"] is True


def test_bootstrap_reports_allow_bench_management_when_disabled(tmp_path: Path) -> None:
    from admin.backend.app import create_app
    from pilot.config.toml_store import BenchTomlStore

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    toml_path = bench_root / "bench.toml"
    config = BenchConfig.from_file(toml_path)
    config.admin.allow_bench_management = False
    BenchTomlStore(toml_path).write(config)

    app = create_app(bench_root)
    app.config["TESTING"] = True
    assert app.test_client().get("/api/v1/bootstrap").get_json()["allow_bench_management"] is False


def test_login_with_sid_sets_httponly_cookie(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/v1/session", json={"sid": issue_login_token("k3y")})
    assert resp.status_code == 201
    assert resp.headers["Location"] == "/api/v1/session"
    assert resp.get_json() == {"authenticated": True, "scope": "bench"}
    cookie = next(h for k, h in resp.headers if k == "Set-Cookie" and h.startswith("sid="))
    assert "HttpOnly" in cookie
    assert "Secure" not in cookie
    assert client.get("/api/v1/benches/").status_code != 401


def test_login_cookie_uses_explicit_secure_cookie_setting(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.application.config["SESSION_COOKIE_SECURE"] = True

    response = client.post("/api/v1/session", json={"sid": issue_login_token("k3y")})

    cookie = next(
        value for key, value in response.headers if key == "Set-Cookie" and value.startswith("sid=")
    )
    assert "Secure" in cookie


def test_setup_session_cookie_uses_explicit_secure_cookie_setting(tmp_path: Path) -> None:
    from admin.backend.app import create_app

    app = create_app(tmp_path)
    app.config.update(TESTING=True, SESSION_COOKIE_SECURE=True)

    response = app.test_client().post(
        "/api/v1/setup/save",
        json={"admin_password": "secret", "mariadb_password": "db-secret"},
    )

    cookie = next(
        value for key, value in response.headers if key == "Set-Cookie" and value.startswith("sid=")
    )
    assert response.status_code == 200
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=Lax" in cookie


def test_secure_cookie_setting_requires_tls_or_configured_proxy(monkeypatch) -> None:
    from admin.backend.app import _secure_cookie_setting

    config = SimpleNamespace(
        production=SimpleNamespace(enabled=True),
        admin=SimpleNamespace(tls=False),
    )
    store = SimpleNamespace(read=lambda: config)

    monkeypatch.setattr(
        "pilot.core.domain_controller.DomainRouteProvider.proxy_servers", lambda: []
    )
    assert _secure_cookie_setting(store) is False

    monkeypatch.setattr(
        "pilot.core.domain_controller.DomainRouteProvider.proxy_servers",
        lambda: ["203.0.113.10"],
    )
    assert _secure_cookie_setting(store) is True

    config.admin.tls = True
    monkeypatch.setattr(
        "pilot.core.domain_controller.DomainRouteProvider.proxy_servers", lambda: []
    )
    assert _secure_cookie_setting(store) is True


def test_login_with_invalid_sid_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/v1/session", json={"sid": issue_login_token("wrong-secret")})
    assert resp.status_code == 401
    assert resp.get_json()["error"]["code"] == "invalid_login_token"
    assert client.get("/api/v1/benches/").status_code == 401


def test_session_creation_requires_a_json_object(tmp_path: Path) -> None:
    response = _client(tmp_path).post("/api/v1/session", json=["secret"])

    assert response.status_code == 400
    assert response.get_json() == {
        "error": {
            "code": "malformed_request",
            "details": {},
            "message": "Expected a JSON object.",
        }
    }


def test_sid_is_single_use(tmp_path: Path) -> None:
    client = _client(tmp_path)
    sid = issue_login_token("k3y")
    assert client.post("/api/v1/session", json={"sid": sid}).status_code == 201
    assert client.post("/api/v1/session", json={"sid": sid}).status_code == 401


def test_login_rate_limited_after_limit(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for _ in range(5):
        assert client.post("/api/v1/session", json={"password": "wrong"}).status_code == 401
    response = client.post("/api/v1/session", json={"password": "wrong"})

    assert response.status_code == 429
    assert response.get_json() == {
        "error": {
            "code": "rate_limit_exceeded",
            "details": {},
            "message": "Too many attempts. Try again later.",
        }
    }


def test_login_rate_limit_is_scoped_to_each_app(tmp_path: Path) -> None:
    first_client = _client(tmp_path / "first")
    for _ in range(5):
        first_client.post("/api/v1/session", json={"password": "wrong"})

    second_client = _client(tmp_path / "second")

    response = second_client.post("/api/v1/session", json={"password": "wrong"})
    assert response.status_code == 401


def test_login_rate_limit_ignores_spoofed_forwarded_ips(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for index in range(5):
        response = client.post(
            "/api/v1/session",
            json={"password": "wrong"},
            headers={"X-Real-IP": f"203.0.113.{index + 1}"},
        )
        assert response.status_code == 401

    response = client.post(
        "/api/v1/session",
        json={"password": "wrong"},
        headers={"X-Real-IP": "203.0.113.99"},
    )
    assert response.status_code == 429


def test_forwarded_headers_are_trusted_only_behind_production_nginx() -> None:
    from admin.backend.app import _trusted_proxy_peers

    development = SimpleNamespace(
        read=lambda: SimpleNamespace(production=SimpleNamespace(enabled=False))
    )
    production = SimpleNamespace(
        read=lambda: SimpleNamespace(production=SimpleNamespace(enabled=True))
    )

    assert _trusted_proxy_peers(development) == ()
    assert _trusted_proxy_peers(production) == ("127.0.0.1", "::1", "")


def test_setup_endpoint_requires_auth_once_password_set(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/api/v1/setup/validate-mariadb", json={}).status_code == 401
    client.set_cookie("sid", issue_token("k3y"))
    assert client.post("/api/v1/setup/validate-mariadb", json={}).status_code != 401


def test_setup_endpoint_open_before_password_set(tmp_path: Path) -> None:
    from admin.backend.app import create_app

    app = create_app(tmp_path)  # no bench.toml → first-time setup
    app.config["TESTING"] = True
    assert app.test_client().post("/api/v1/setup/validate-mariadb", json={}).status_code != 401


def test_setup_endpoint_fails_closed_when_config_is_corrupt(tmp_path: Path) -> None:
    from admin.backend.app import create_app

    (tmp_path / "bench.toml").write_text("[bench\n")
    app = create_app(tmp_path)
    app.config["TESTING"] = True

    response = app.test_client().post("/api/v1/setup/validate-mariadb", json={})

    assert response.status_code == 503


# ── scoped JWT ────────────────────────────────────────────────────────────────


def test_issue_token_defaults_to_bench_scope() -> None:
    claims = decode_token(issue_token("k3y"), "k3y")
    assert claims["scope"] == "bench"
    assert "site" not in claims


def test_issue_token_with_site_scope() -> None:
    claims = decode_token(issue_token("k3y", scope="site", site="example.com"), "k3y")
    assert claims["scope"] == "site"
    assert claims["site"] == "example.com"


def test_has_scope_bench_token_allows_any_site() -> None:
    claims = decode_token(issue_token("k3y"), "k3y")
    assert has_scope(claims, "example.com")
    assert has_scope(claims, "other.com")


def test_has_scope_site_token_allows_matching_site() -> None:
    claims = decode_token(issue_token("k3y", scope="site", site="example.com"), "k3y")
    assert has_scope(claims, "example.com")


def test_has_scope_site_token_rejects_different_site() -> None:
    claims = decode_token(issue_token("k3y", scope="site", site="example.com"), "k3y")
    assert not has_scope(claims, "other.com")


def test_has_scope_none_claims_rejected() -> None:
    assert not has_scope(None, "example.com")


def test_issue_site_token_creates_scoped_token() -> None:
    claims = decode_token(issue_site_token("k3y", "example.com"), "k3y")
    assert claims["scope"] == "site"
    assert claims["site"] == "example.com"


def test_issue_site_token_requires_site() -> None:
    with pytest.raises(ValueError):
        issue_site_token("k3y", "")


def test_issue_site_token_custom_ttl() -> None:
    claims = decode_token(issue_site_token("k3y", "example.com", ttl=3600), "k3y")
    assert claims["site"] == "example.com"
    assert claims["exp"] - claims["iat"] == 3600


@pytest.mark.parametrize(
    ("scope", "site"),
    [
        ("site", "example.com"),
        ("unknown", None),
    ],
)
def test_non_bench_token_cannot_access_bench_route(
    tmp_path: Path,
    scope: str,
    site: str | None,
) -> None:
    client = _client(tmp_path)
    token = issue_token("k3y", scope=scope, site=site)

    response = client.get("/api/v1/tasks", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403


def test_require_scope_allows_unscoped_token(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/test-scoped")
    @require_scope("example.com")
    def scoped_view():
        return jsonify({"ok": True})

    client = app.test_client()
    client.set_cookie("sid", issue_token("k3y"))
    assert client.get("/api/v1/test-scoped").status_code == 200


def test_require_scope_allows_matching_scoped_token(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/test-scoped")
    @require_scope("example.com")
    def scoped_view():
        return jsonify({"ok": True})

    client = app.test_client()
    client.set_cookie("sid", issue_token("k3y", scope="site", site="example.com"))
    assert client.get("/api/v1/test-scoped").status_code == 200


def test_require_scope_rejects_mismatched_scoped_token(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/test-scoped")
    @require_scope("example.com")
    def scoped_view():
        return jsonify({"ok": True})

    client = app.test_client()
    client.set_cookie("sid", issue_token("k3y", scope="site", site="other.com"))
    assert client.get("/api/v1/test-scoped").status_code == 403


def test_current_site_scope_returns_site_from_claims(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import current_site_scope, require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/test-scope")
    @require_scope("example.com")
    def scope_view():
        return jsonify({"site": current_site_scope()})

    client = app.test_client()
    client.set_cookie("sid", issue_token("k3y", scope="site", site="example.com"))
    assert client.get("/api/v1/test-scope").get_json()["site"] == "example.com"


def test_current_site_scope_returns_none_for_unscoped(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import current_site_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/test-scope")
    def scope_view():
        return jsonify({"site": current_site_scope()})

    client = app.test_client()
    client.set_cookie("sid", issue_token("k3y"))
    assert client.get("/api/v1/test-scope").get_json()["site"] is None


def test_bearer_token_authenticates(tmp_path: Path) -> None:
    client = _client(tmp_path)
    token = issue_token("k3y")
    resp = client.get("/api/v1/benches/", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code != 401


def test_bearer_token_with_site_scope(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/test-scoped")
    @require_scope("example.com")
    def scoped_view():
        return jsonify({"ok": True})

    client = app.test_client()
    token = issue_site_token("k3y", "example.com")
    resp = client.get("/api/v1/test-scoped", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_bearer_token_wrong_site_rejected(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/test-scoped")
    @require_scope("example.com")
    def scoped_view():
        return jsonify({"ok": True})

    client = app.test_client()
    token = issue_site_token("k3y", "other.com")
    resp = client.get("/api/v1/test-scoped", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_require_scope_with_callable(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/sites/<name>/action")
    @require_scope(lambda kw: kw["name"])
    def scoped_view(name):
        return jsonify({"ok": True, "site": name})

    client = app.test_client()
    client.set_cookie("sid", issue_token("k3y", scope="site", site="example.com"))
    assert client.get("/api/v1/sites/example.com/action").status_code == 200
    assert client.get("/api/v1/sites/other.com/action").status_code == 403
