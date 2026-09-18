from __future__ import annotations

import secrets
import time
from pathlib import Path
from types import SimpleNamespace

import jwt as pyjwt
import pytest

from admin.backend.internal.session import Session
from pilot.config import BenchConfig
from pilot.core.bench import Bench


def _session_token(secret: str = "k3y", scope: str = "bench", site: str | None = None, ttl: int = 300) -> str:
    """A token signed with ``secret``, for authenticating a test client."""
    payload = {"sub": "admin", "scope": scope, "exp": int(time.time()) + ttl}
    if site:
        payload["site"] = site
    return pyjwt.encode(payload, secret, algorithm="HS256")


def _login_token(secret: str = "k3y") -> str:
    """A single-use ?sid= sign-in token signed with ``secret``."""
    payload = {
        "sub": "admin",
        "scope": "bench",
        "jti": secrets.token_urlsafe(8),
        "exp": int(time.time()) + 300,
    }
    return pyjwt.encode(payload, secret, algorithm="HS256")


def _bench(tmp_path: Path, password: str = "secret") -> Bench:
    toml_path = tmp_path / "bench.toml"
    toml_path.write_text(BenchConfig.from_flat(tmp_path.name, {"admin_password": password}).dumps())
    return _load_bench(tmp_path)


def _load_bench(tmp_path: Path) -> Bench:
    return Bench(BenchConfig.from_file(tmp_path / "bench.toml"), tmp_path)


def _initialized_bench(bench_dir: Path, password: str, jwt_secret: str) -> None:
    bench_dir.mkdir(parents=True, exist_ok=True)
    toml_path = bench_dir / "bench.toml"
    toml_path.write_text(
        BenchConfig.from_flat(bench_dir.name, {"admin_enabled": True, "admin_password": password}).dumps()
    )
    config = BenchConfig.from_file(toml_path)
    config.admin.jwt_secret = jwt_secret
    config.write(toml_path)
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


def _signed_in_client(tmp_path: Path, jwt_secret: str = "k3y"):
    client = _client(tmp_path, jwt_secret)
    client.set_cookie("sid", _session_token(jwt_secret))
    return client


def test_valid_jwt_cookie_authenticates(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    assert client.get("/api/v1/auth/session").get_json() == {
        "authenticated": True,
        "scope": "bench",
    }
    assert client.get("/api/v1/benches").status_code != 401


def test_invalid_jwt_cookie_stays_unauthenticated(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token("wrong-secret"))
    assert client.get("/api/v1/auth/session").get_json() == {"authenticated": False}
    assert client.get("/api/v1/benches").status_code == 401


def test_bootstrap_does_not_report_session_state(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())

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
    assert client.get("/api/v1/auth/session").get_json() == {"authenticated": False}


def test_delete_session_clears_cookie(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())

    response = client.delete("/api/v1/auth/session")

    assert response.status_code == 204
    assert response.data == b""
    assert client.get("/api/v1/auth/session").get_json() == {"authenticated": False}


def test_delete_session_revokes_the_token(tmp_path: Path) -> None:
    from admin.backend.internal.session import RevokedTokens, Session

    client = _client(tmp_path)
    bench = Bench(tmp_path / "benches" / "current")
    token, jti = Session(bench).issue_session_token()
    client.set_cookie("sid", token)

    response = client.delete("/api/v1/auth/session")

    assert response.status_code == 204
    assert jti in RevokedTokens(bench)


def test_delete_session_without_a_cookie_still_clears_it(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.delete("/api/v1/auth/session")

    assert response.status_code == 204


def test_bootstrap_reports_bench_db_type(tmp_path: Path) -> None:
    # The engine is a bench-wide property; the admin reads it from bootstrap to
    # show one bench-level badge instead of a per-site one.
    client = _signed_in_client(tmp_path)
    assert client.get("/api/v1/bootstrap").get_json()["db_type"] == "mariadb"


def test_bootstrap_reports_sanitized_task_activity(tmp_path: Path) -> None:
    body = _signed_in_client(tmp_path).get("/api/v1/bootstrap").get_json()

    assert body["task_worker"] == {
        "active": False,
        "desired": "running",
        "status": "not-started",
        "uncertain": False,
    }
    assert "current_task_id" not in body["task_worker"]


def test_bootstrap_reports_postgres_engine(tmp_path: Path) -> None:
    from admin.backend.app import create_app
    bench_root = tmp_path / "benches" / "pg"
    _initialized_bench(bench_root, "secret", "k3y")
    toml_path = bench_root / "bench.toml"
    config = BenchConfig.from_file(toml_path)
    config.db_type = "postgres"
    config.write(toml_path)

    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", _session_token())
    assert client.get("/api/v1/bootstrap").get_json()["db_type"] == "postgres"


def test_bootstrap_reports_allow_bench_management_default_true(tmp_path: Path) -> None:
    client = _signed_in_client(tmp_path)
    assert client.get("/api/v1/bootstrap").get_json()["allow_bench_management"] is True


def test_bootstrap_tells_an_unauthenticated_caller_only_what_the_login_page_needs(
    tmp_path: Path,
) -> None:
    body = _client(tmp_path).get("/api/v1/bootstrap").get_json()

    assert set(body) == {"mode", "enabled", "name"}


def test_bootstrap_reports_allow_bench_management_when_disabled(tmp_path: Path) -> None:
    from admin.backend.app import create_app
    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    toml_path = bench_root / "bench.toml"
    config = BenchConfig.from_file(toml_path)
    config.admin.allow_bench_management = False
    config.write(toml_path)

    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", _session_token())
    assert client.get("/api/v1/bootstrap").get_json()["allow_bench_management"] is False


def test_login_with_sid_sets_httponly_cookie(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/v1/auth/session", json={"sid": _login_token()})
    assert resp.status_code == 201
    assert resp.headers["Location"] == "/api/v1/auth/session"
    assert resp.get_json() == {"authenticated": True, "scope": "bench"}
    cookie = next(h for k, h in resp.headers if k == "Set-Cookie" and h.startswith("sid="))
    assert "HttpOnly" in cookie
    assert "Secure" not in cookie
    assert client.get("/api/v1/benches").status_code != 401


def test_setup_link_signs_in_for_a_shorter_session_than_a_password(tmp_path: Path) -> None:
    import jwt as decoder

    client = _client(tmp_path)
    bench = Bench(tmp_path / "benches" / "current")
    link = Session(bench).issue_setup_link_token()

    response = client.post("/api/v1/auth/session", json={"sid": link})

    assert response.status_code == 201
    cookie = next(h for k, h in response.headers if k == "Set-Cookie" and h.startswith("sid="))
    session_token = cookie.removeprefix("sid=").split(";")[0]
    claims = decoder.decode(session_token, "k3y", algorithms=["HS256"])
    # The session inherits the link's remaining life (~3h), not the 24h of a password login.
    lifetime = claims["exp"] - claims["iat"]
    assert Session.SETUP_SESSION_TTL - 5 <= lifetime <= Session.SETUP_SESSION_TTL
    assert lifetime < Session.DEFAULT_TTL
    assert client.get("/api/v1/setup/configuration").status_code == 200


def test_a_setup_link_cannot_be_redeemed_twice(tmp_path: Path) -> None:
    client = _client(tmp_path)
    link = Session(Bench(tmp_path / "benches" / "current")).issue_setup_link_token()

    first = client.post("/api/v1/auth/session", json={"sid": link})
    second = client.post("/api/v1/auth/session", json={"sid": link})

    assert first.status_code == 201
    assert second.status_code == 401


def test_password_login_records_session_issued(tmp_path: Path) -> None:
    from pilot.core.bench.audit_log import AuditLog

    client = _client(tmp_path)
    assert client.post("/api/v1/auth/session", json={"password": "secret"}).status_code == 201

    issued = AuditLog(Bench(tmp_path / "benches" / "current")).entries(entry_type="session")
    assert len(issued) == 1
    assert issued[0]["event"] == "issued"
    assert issued[0]["via"] == "password"
    assert issued[0]["jti"]


def test_sid_login_records_redeemed_and_issued(tmp_path: Path) -> None:
    from pilot.core.bench.audit_log import AuditLog

    client = _client(tmp_path)
    assert client.post("/api/v1/auth/session", json={"sid": _login_token()}).status_code == 201

    entries = AuditLog(Bench(tmp_path / "benches" / "current")).entries(entry_type="session")
    assert {e["event"] for e in entries} == {"issued", "login_redeemed"}
    issued = next(e for e in entries if e["event"] == "issued")
    assert issued["via"] == "login_link"


def test_login_cookie_uses_explicit_is_secure_cookie(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.application.config["SESSION_COOKIE_SECURE"] = True

    response = client.post("/api/v1/auth/session", json={"sid": _login_token()})

    cookie = next(
        value for key, value in response.headers if key == "Set-Cookie" and value.startswith("sid=")
    )
    assert "Secure" in cookie


def test_is_secure_cookie_requires_tls_or_configured_proxy(monkeypatch) -> None:
    from admin.backend.app import is_secure_cookie

    config = SimpleNamespace(
        production=SimpleNamespace(enabled=True),
        admin=SimpleNamespace(tls=False),
    )
    monkeypatch.setattr(BenchConfig, "read", lambda bench_root: config)
    unused_root = Path("unused")

    monkeypatch.setattr("pilot.core.adapters.domain_provider.DomainRouteProvider.proxy_servers", lambda: [])
    assert is_secure_cookie(unused_root) is False

    monkeypatch.setattr(
        "pilot.core.adapters.domain_provider.DomainRouteProvider.proxy_servers",
        lambda: ["203.0.113.10"],
    )
    assert is_secure_cookie(unused_root) is True

    config.admin.tls = True
    monkeypatch.setattr("pilot.core.adapters.domain_provider.DomainRouteProvider.proxy_servers", lambda: [])
    assert is_secure_cookie(unused_root) is True


def test_login_with_invalid_sid_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/v1/auth/session", json={"sid": _login_token("wrong-secret")})
    assert resp.status_code == 401
    assert resp.get_json()["error"]["code"] == "invalid_login_token"
    assert client.get("/api/v1/benches").status_code == 401


def test_session_creation_requires_a_json_object(tmp_path: Path) -> None:
    response = _client(tmp_path).post("/api/v1/auth/session", json=["secret"])

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
    sid = _login_token()
    assert client.post("/api/v1/auth/session", json={"sid": sid}).status_code == 201
    assert client.post("/api/v1/auth/session", json={"sid": sid}).status_code == 401


def test_login_rate_limited_after_limit(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for _ in range(5):
        assert client.post("/api/v1/auth/session", json={"password": "wrong"}).status_code == 401
    response = client.post("/api/v1/auth/session", json={"password": "wrong"})

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
        first_client.post("/api/v1/auth/session", json={"password": "wrong"})

    second_client = _client(tmp_path / "second")

    response = second_client.post("/api/v1/auth/session", json={"password": "wrong"})
    assert response.status_code == 401


def test_login_rate_limit_ignores_spoofed_forwarded_ips(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for index in range(5):
        response = client.post(
            "/api/v1/auth/session",
            json={"password": "wrong"},
            headers={"X-Real-IP": f"203.0.113.{index + 1}"},
        )
        assert response.status_code == 401

    response = client.post(
        "/api/v1/auth/session",
        json={"password": "wrong"},
        headers={"X-Real-IP": "203.0.113.99"},
    )
    assert response.status_code == 429


def test_forwarded_headers_are_trusted_only_behind_production_nginx(monkeypatch) -> None:
    from admin.backend.app import trusted_proxy_peers

    development = SimpleNamespace(production=SimpleNamespace(enabled=False))
    production = SimpleNamespace(production=SimpleNamespace(enabled=True))
    unused_root = Path("unused")

    monkeypatch.setattr(BenchConfig, "read", lambda bench_root: development)
    assert trusted_proxy_peers(unused_root) == ()

    monkeypatch.setattr(BenchConfig, "read", lambda bench_root: production)
    assert trusted_proxy_peers(unused_root) == ("127.0.0.1", "::1", "")


def test_setup_endpoint_requires_auth_once_password_set(tmp_path: Path) -> None:
    client = _client(tmp_path)
    path = "/api/v1/setup/database-validations"
    assert client.post(path, json={"engine": "mariadb"}).status_code == 401
    client.set_cookie("sid", _session_token())
    assert client.post(path, json={"engine": "mariadb"}).status_code != 401


def test_setup_endpoint_open_before_password_set(tmp_path: Path) -> None:
    from admin.backend.app import create_app

    app = create_app(tmp_path)  # no bench.toml → first-time setup
    app.config["TESTING"] = True
    response = app.test_client().post(
        "/api/v1/setup/database-validations",
        json={"engine": "mariadb"},
    )
    assert response.status_code != 401


def test_setup_endpoint_fails_closed_when_config_is_corrupt(tmp_path: Path) -> None:
    from admin.backend.app import create_app

    (tmp_path / "bench.toml").write_text("[bench\n")
    app = create_app(tmp_path)
    app.config["TESTING"] = True

    response = app.test_client().post(
        "/api/v1/setup/database-validations",
        json={"engine": "mariadb"},
    )

    assert response.status_code == 503


def test_has_scope_bench_token_allows_any_site() -> None:
    assert Session.has_scope({"scope": "bench"}, "example.com")
    assert Session.has_scope({"scope": "bench"}, "other.com")


def test_has_scope_site_token_allows_matching_site() -> None:
    assert Session.has_scope({"scope": "site", "site": "example.com"}, "example.com")


def test_has_scope_site_token_rejects_different_site() -> None:
    assert not Session.has_scope({"scope": "site", "site": "example.com"}, "other.com")


def test_has_scope_none_claims_rejected() -> None:
    assert not Session.has_scope(None, "example.com")


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
    token = _session_token(scope=scope, site=site)

    response = client.get("/api/v1/tasks", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403


def test_require_scope_allows_unscoped_token(tmp_path: Path) -> None:
    from flask import jsonify

    from admin.backend.app import create_app
    from admin.backend.middleware import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/test-scoped")
    @require_scope("example.com")
    def scoped_view():
        return jsonify({"ok": True})

    client = app.test_client()
    client.set_cookie("sid", _session_token())
    assert client.get("/api/v1/test-scoped").status_code == 200


def test_require_scope_allows_matching_scoped_token(tmp_path: Path) -> None:
    from flask import jsonify

    from admin.backend.app import create_app
    from admin.backend.middleware import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/test-scoped")
    @require_scope("example.com")
    def scoped_view():
        return jsonify({"ok": True})

    client = app.test_client()
    client.set_cookie("sid", _session_token(scope="site", site="example.com"))
    assert client.get("/api/v1/test-scoped").status_code == 200


def test_require_scope_rejects_mismatched_scoped_token(tmp_path: Path) -> None:
    from flask import jsonify

    from admin.backend.app import create_app
    from admin.backend.middleware import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/test-scoped")
    @require_scope("example.com")
    def scoped_view():
        return jsonify({"ok": True})

    client = app.test_client()
    client.set_cookie("sid", _session_token(scope="site", site="other.com"))
    assert client.get("/api/v1/test-scoped").status_code == 403


def test_current_site_scope_returns_site_from_claims(tmp_path: Path) -> None:
    from flask import jsonify

    from admin.backend.app import create_app
    from admin.backend.middleware import current_site_scope, require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/test-scope")
    @require_scope("example.com")
    def scope_view():
        return jsonify({"site": current_site_scope()})

    client = app.test_client()
    client.set_cookie("sid", _session_token(scope="site", site="example.com"))
    assert client.get("/api/v1/test-scope").get_json()["site"] == "example.com"


def test_current_site_scope_returns_none_for_unscoped(tmp_path: Path) -> None:
    from flask import jsonify

    from admin.backend.app import create_app
    from admin.backend.middleware import current_site_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/test-scope")
    def scope_view():
        return jsonify({"site": current_site_scope()})

    client = app.test_client()
    client.set_cookie("sid", _session_token())
    assert client.get("/api/v1/test-scope").get_json()["site"] is None


def test_bearer_token_authenticates(tmp_path: Path) -> None:
    client = _client(tmp_path)
    token = _session_token()
    resp = client.get("/api/v1/benches", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code != 401


def test_bearer_token_with_site_scope(tmp_path: Path) -> None:
    from flask import jsonify

    from admin.backend.app import create_app
    from admin.backend.middleware import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/test-scoped")
    @require_scope("example.com")
    def scoped_view():
        return jsonify({"ok": True})

    client = app.test_client()
    token = _session_token(scope="site", site="example.com")
    resp = client.get("/api/v1/test-scoped", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_bearer_token_wrong_site_rejected(tmp_path: Path) -> None:
    from flask import jsonify

    from admin.backend.app import create_app
    from admin.backend.middleware import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/test-scoped")
    @require_scope("example.com")
    def scoped_view():
        return jsonify({"ok": True})

    client = app.test_client()
    token = _session_token(scope="site", site="other.com")
    resp = client.get("/api/v1/test-scoped", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_require_scope_with_callable(tmp_path: Path) -> None:
    from flask import jsonify

    from admin.backend.app import create_app
    from admin.backend.middleware import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/v1/sites/<name>/action")
    @require_scope(lambda kw: kw["name"])
    def scoped_view(name):
        return jsonify({"ok": True, "site": name})

    client = app.test_client()
    client.set_cookie("sid", _session_token(scope="site", site="example.com"))
    assert client.get("/api/v1/sites/example.com/action").status_code == 200
    assert client.get("/api/v1/sites/other.com/action").status_code == 403


def test_revoke_session_endpoint_revokes_active_jti(tmp_path: Path) -> None:
    from admin.backend.internal.session import ActiveTokens, RevokedTokens, Session

    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    bench = Bench(tmp_path / "benches" / "current")
    _, jti = Session(bench).issue_session_token()
    assert jti in ActiveTokens(bench)

    assert client.post(f"/api/v1/auth/sessions/revoke/{jti}").status_code == 204
    assert jti in RevokedTokens(bench)


def test_revoke_session_unknown_jti_is_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    assert client.post("/api/v1/auth/sessions/revoke/nope").status_code == 404


def test_revoke_session_requires_authentication(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/api/v1/auth/sessions/revoke/x").status_code == 401


def test_revoke_session_is_audited_with_request_context(tmp_path: Path) -> None:
    from admin.backend.internal.session import Session
    from pilot.core.bench.audit_log import AuditLog

    client = _client(tmp_path)
    bench = Bench(tmp_path / "benches" / "current")
    actor_token, actor_jti = Session(bench).issue_session_token()
    client.set_cookie("sid", actor_token)
    _, target_jti = Session(bench).issue_session_token()

    assert client.post(f"/api/v1/auth/sessions/revoke/{target_jti}").status_code == 204

    revoked = [e for e in AuditLog(bench).entries(entry_type="session") if e.get("event") == "revoked"]
    assert len(revoked) == 1
    assert revoked[0]["jti"] == target_jti  # the token acted on
    assert revoked[0]["actor_jti"] == actor_jti  # who did it (their session)
    assert revoked[0]["ip"]
    # actor is the user.email claim; local admin tokens carry none.
    assert revoked[0]["actor"] is None


def test_sessions_reports_current_session_jti(tmp_path: Path) -> None:
    from admin.backend.internal.session import Session

    client = _client(tmp_path)
    token, jti = Session(Bench(tmp_path / "benches" / "current")).issue_session_token()
    client.set_cookie("sid", token)

    data = client.get("/api/v1/auth/sessions").get_json()
    assert data["current_jti"] == jti


_NEW_PASSWORD = "N3wSecret!"


def _change_password(client, **payload):
    return client.post("/api/v1/auth/password", json=payload)


def test_change_admin_password_writes_config_without_revoking_sessions(tmp_path: Path) -> None:
    from admin.backend.internal.session import RevokedTokens, Session

    client = _client(tmp_path)
    bench_root = tmp_path / "benches" / "current"
    bench = Bench(bench_root)
    token, old_jti = Session(bench).issue_session_token()
    client.set_cookie("sid", token)

    response = _change_password(client, new_password=_NEW_PASSWORD)

    assert response.status_code == 200
    assert response.get_json() == {}
    stored = BenchConfig.from_file(bench_root / "bench.toml").admin
    assert stored.verify_password(_NEW_PASSWORD)
    assert _NEW_PASSWORD not in stored.password  # bench.toml keeps a verifier, not the password
    assert old_jti not in RevokedTokens(bench)


def test_change_admin_password_keeps_caller_signed_in(tmp_path: Path) -> None:
    from admin.backend.internal.session import Session

    client = _client(tmp_path)
    token, _ = Session(Bench(tmp_path / "benches" / "current")).issue_session_token()
    client.set_cookie("sid", token)

    response = _change_password(client, new_password=_NEW_PASSWORD)

    # Password change alone never touches sessions - no new cookie is issued.
    assert not any(key == "Set-Cookie" for key, _ in response.headers)
    assert client.get("/api/v1/auth/session").get_json()["authenticated"] is True


@pytest.mark.parametrize(
    "new_password",
    ["Sh0rt!", "n3wsecret!", "N3WSECRET!", "NewSecret!", "N3wSecret"],
)
def test_change_admin_password_enforces_strength(tmp_path: Path, new_password: str) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())

    response = _change_password(client, new_password=new_password)

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_password"


def test_change_admin_password_rejects_unchanged_password(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())

    response = _change_password(client, new_password="secret")

    assert response.status_code == 422
    assert "differ" in response.get_json()["error"]["message"]


def test_change_admin_password_requires_authentication(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = _change_password(client, new_password=_NEW_PASSWORD)

    assert response.status_code == 401
    assert BenchConfig.from_file(tmp_path / "benches" / "current" / "bench.toml").admin.verify_password("secret")


def test_change_admin_password_rejects_site_scoped_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token(scope="site", site="example.com"))

    response = _change_password(client, new_password=_NEW_PASSWORD)

    assert response.status_code == 403


def test_change_admin_password_is_audited(tmp_path: Path) -> None:
    from admin.backend.internal.session import Session
    from pilot.core.bench.audit_log import AuditLog

    client = _client(tmp_path)
    bench = Bench(tmp_path / "benches" / "current")
    actor_token, actor_jti = Session(bench).issue_session_token()
    client.set_cookie("sid", actor_token)

    assert _change_password(client, new_password=_NEW_PASSWORD).status_code == 200

    entries = AuditLog(bench).entries(entry_type="session")
    changed = [entry for entry in entries if entry.get("event") == "admin_password_changed"]
    assert len(changed) == 1
    assert changed[0]["actor_jti"] == actor_jti


def _revoke_all_sessions(client):
    return client.post("/api/v1/auth/sessions/revoke/all")


def test_revoke_all_sessions_revokes_and_reissues(tmp_path: Path) -> None:
    from admin.backend.internal.session import RevokedTokens, Session

    client = _client(tmp_path)
    bench = Bench(tmp_path / "benches" / "current")
    token, old_jti = Session(bench).issue_session_token()
    client.set_cookie("sid", token)

    response = _revoke_all_sessions(client)

    assert response.status_code == 200
    assert response.get_json() == {"revoked_sessions": 1}
    assert old_jti in RevokedTokens(bench)


def test_revoke_all_sessions_keeps_caller_signed_in(tmp_path: Path) -> None:
    from admin.backend.internal.session import Session

    client = _client(tmp_path)
    token, _ = Session(Bench(tmp_path / "benches" / "current")).issue_session_token()
    client.set_cookie("sid", token)

    response = _revoke_all_sessions(client)

    cookie = next(
        value for key, value in response.headers if key == "Set-Cookie" and value.startswith("sid=")
    )
    assert "HttpOnly" in cookie
    # The replacement cookie is live; the revoked original is not.
    assert client.get("/api/v1/auth/session").get_json()["authenticated"] is True


def test_revoke_all_sessions_requires_authentication(tmp_path: Path) -> None:
    client = _client(tmp_path)

    assert _revoke_all_sessions(client).status_code == 401


def test_revoke_all_sessions_rejects_site_scoped_token(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token(scope="site", site="example.com"))

    assert _revoke_all_sessions(client).status_code == 403


def test_revoke_all_sessions_is_audited(tmp_path: Path) -> None:
    from admin.backend.internal.session import Session
    from pilot.core.bench.audit_log import AuditLog

    client = _client(tmp_path)
    bench = Bench(tmp_path / "benches" / "current")
    actor_token, actor_jti = Session(bench).issue_session_token()
    client.set_cookie("sid", actor_token)

    assert _revoke_all_sessions(client).status_code == 200

    entries = AuditLog(bench).entries(entry_type="session")
    changed = [entry for entry in entries if entry.get("event") == "other_sessions_revoked"]
    assert len(changed) == 1
    assert changed[0]["actor_jti"] == actor_jti
    assert changed[0]["revoked_sessions"] == 1
    assert changed[0]["jti"] != actor_jti


def test_settings_patch_ignores_admin_password(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())

    assert client.patch("/api/v1/settings", json={"admin_password": _NEW_PASSWORD}).status_code == 200
    assert BenchConfig.from_file(tmp_path / "benches" / "current" / "bench.toml").admin.verify_password("secret")


def _enroll_device(client, name: str = "phone") -> dict:
    """Register and confirm a device through the API, returning the enrollment payload."""
    import pyotp

    response = client.post("/api/v1/auth/two-factor/enrollment", json={"name": name})
    assert response.status_code == 200
    enrollment = response.get_json()
    # Confirm with the previous step so the current code stays unspent for the test body.
    code = pyotp.TOTP(enrollment["secret"]).at(int(time.time()) - 30)
    confirmed = client.post(f"/api/v1/auth/two-factor/{enrollment['name']}", json={"otp": code})
    assert confirmed.status_code == 200
    enrollment["confirmation"] = confirmed.get_json()
    return enrollment


def test_two_factor_starts_disabled_with_no_devices(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())

    body = client.get("/api/v1/auth/two-factor").get_json()
    assert body["enabled"] is False
    assert body["credentials"] == []
    assert body["recovery_codes_remaining"] == 0


def test_two_factor_enrollment_returns_a_secret_and_url(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())

    body = client.post(
        "/api/v1/auth/two-factor/enrollment", json={"name": "Ops laptop"}
    ).get_json()

    assert body["secret"] in body["provisioning_url"]
    assert body["provisioning_url"].startswith("otpauth://totp/")
    assert body["name"] == "Ops laptop"


def test_two_factor_enrollment_requires_a_label(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())

    response = client.post(
        "/api/v1/auth/two-factor/enrollment", json={"name": " "}
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_device_name"


def test_a_device_is_only_confirmed_with_a_valid_code(tmp_path: Path) -> None:
    import pyotp

    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    enrollment = client.post(
        "/api/v1/auth/two-factor/enrollment", json={"name": "phone"}
    ).get_json()

    rejected = client.post(f"/api/v1/auth/two-factor/{enrollment['name']}", json={"otp": "000000"})
    assert rejected.status_code == 422
    assert client.get("/api/v1/auth/two-factor").get_json()["enabled"] is False

    code = pyotp.TOTP(enrollment["secret"]).now()
    accepted = client.post(f"/api/v1/auth/two-factor/{enrollment['name']}", json={"otp": code})
    assert accepted.status_code == 200
    assert accepted.get_json()["enabled"] is True


def test_enabling_two_factor_revokes_existing_sessions(tmp_path: Path) -> None:
    """Tokens issued before 2FA would otherwise skip it until they expired."""
    from admin.backend.internal.session import RevokedTokens, Session

    client = _client(tmp_path)
    bench = Bench(tmp_path / "benches" / "current")
    token, jti = Session(bench).issue_session_token()
    client.set_cookie("sid", token)

    _enroll_device(client)

    assert jti in RevokedTokens(bench)


def test_adding_a_second_device_keeps_sessions(tmp_path: Path) -> None:
    from admin.backend.internal.session import RevokedTokens, Session

    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    _enroll_device(client, "phone")

    bench = Bench(tmp_path / "benches" / "current")
    token, jti = Session(bench).issue_session_token()
    client.set_cookie("sid", token)
    _enroll_device(client, "laptop")

    assert jti not in RevokedTokens(bench)


def test_devices_are_listed_without_secrets(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    enrollment = _enroll_device(client, "Ops laptop")

    body = client.get("/api/v1/auth/two-factor").get_json()

    assert [row["name"] for row in body["credentials"]] == ["Ops laptop"]
    assert enrollment["secret"] not in str(body)


def test_removing_a_device_turns_two_factor_off(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    enrollment = _enroll_device(client)
    path = f"/api/v1/auth/two-factor/{enrollment['name']}"

    response = client.delete(path)
    assert response.status_code == 200
    body = response.get_json()
    assert body["enabled"] is False
    assert body["credentials"] == []


def test_removing_an_unknown_device_is_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())

    response = client.delete("/api/v1/auth/two-factor/nope")

    assert response.status_code == 404


def test_two_factor_routes_require_authentication(tmp_path: Path) -> None:
    client = _client(tmp_path)

    assert client.get("/api/v1/auth/two-factor").status_code == 401
    assert client.post("/api/v1/auth/two-factor/enrollment", json={}).status_code == 401
    assert client.post("/api/v1/auth/two-factor/x", json={}).status_code == 401
    assert client.delete("/api/v1/auth/two-factor/x", json={}).status_code == 401


def test_two_factor_changes_are_audited(tmp_path: Path) -> None:
    from pilot.core.bench.audit_log import AuditLog

    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    enrollment = _enroll_device(client)
    client.delete(f"/api/v1/auth/two-factor/{enrollment['name']}")

    bench = Bench(tmp_path / "benches" / "current")
    events = [e["event"] for e in AuditLog(bench).entries(entry_type="session")]
    assert "two_factor_device_added" in events
    assert "two_factor_device_removed" in events


def test_first_device_returns_recovery_codes_once(tmp_path: Path) -> None:
    """The codes are minted on enable, while someone is present to save them."""
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())

    body = _enroll_device(client)["confirmation"]

    assert len(body["recovery_codes"]) == 10
    assert body["recovery_codes_remaining"] == 10
    # A second device must not mint a new set.
    second = _enroll_device(client, "laptop")["confirmation"]
    assert "recovery_codes" not in second



def test_regenerating_replaces_the_recovery_codes(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    original = _enroll_device(client)["confirmation"]["recovery_codes"]

    accepted = client.post("/api/v1/auth/two-factor/recovery-codes")
    assert accepted.status_code == 200
    assert set(accepted.get_json()["recovery_codes"]).isdisjoint(original)




def test_enabling_two_factor_keeps_the_enroller_signed_in(tmp_path: Path) -> None:
    """Revoking every session would sign out the admin who just enabled it."""
    from admin.backend.internal.session import Session

    client = _client(tmp_path)
    bench = Bench(tmp_path / "benches" / "current")
    token, _ = Session(bench).issue_session_token()
    client.set_cookie("sid", token)

    _enroll_device(client)

    assert client.get("/api/v1/auth/two-factor").status_code == 200


def _current_code(bench_root: Path, secret_index: int = -1) -> str:
    """A live code for one enrolled device, read straight from the store."""
    import json

    import pyotp

    data = json.loads((bench_root / ".totp-credentials.json").read_text())
    confirmed = [entry for entry in data.values() if entry.get("confirmed_at")]
    return pyotp.TOTP(confirmed[secret_index]["secret"]).now()


def test_login_asks_for_a_code_once_two_factor_is_on(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    _enroll_device(client)
    client.delete("/api/v1/auth/session")

    response = client.post("/api/v1/auth/session", json={"password": "secret"})

    assert response.status_code == 200
    assert response.get_json() == {"authenticated": False, "two_factor_required": True}
    assert client.get("/api/v1/auth/two-factor").status_code == 401


def test_login_succeeds_with_a_valid_code(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    _enroll_device(client)
    bench_root = tmp_path / "benches" / "current"
    client.delete("/api/v1/auth/session")

    response = client.post(
        "/api/v1/auth/session", json={"password": "secret", "otp": _current_code(bench_root)}
    )

    assert response.status_code == 201
    assert response.get_json()["authenticated"] is True
    assert client.get("/api/v1/auth/two-factor").status_code == 200


def test_login_rejects_a_wrong_code(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    _enroll_device(client)
    client.delete("/api/v1/auth/session")

    response = client.post("/api/v1/auth/session", json={"password": "secret", "otp": "000000"})

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "invalid_otp"
    assert client.get("/api/v1/auth/two-factor").status_code == 401


def test_login_accepts_a_recovery_code(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    codes = _enroll_device(client)["confirmation"]["recovery_codes"]
    client.delete("/api/v1/auth/session")

    response = client.post("/api/v1/auth/session", json={"password": "secret", "otp": codes[0]})

    assert response.status_code == 201
    # Spent on use, so the same code cannot sign in twice.
    client.delete("/api/v1/auth/session")
    assert client.post("/api/v1/auth/session", json={"password": "secret", "otp": codes[0]}).status_code == 401


def test_a_wrong_password_is_rejected_before_the_code_is_considered(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    _enroll_device(client)
    bench_root = tmp_path / "benches" / "current"
    client.delete("/api/v1/auth/session")

    response = client.post(
        "/api/v1/auth/session", json={"password": "wrong", "otp": _current_code(bench_root)}
    )

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "invalid_credentials"


def test_login_link_bypasses_the_second_factor(tmp_path: Path) -> None:
    """`pilot generate-session` runs on the server, so its holder already had shell access."""
    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    _enroll_device(client)
    client.delete("/api/v1/auth/session")

    assert client.post("/api/v1/auth/session", json={"sid": _login_token()}).status_code == 201


def test_a_recovery_sign_in_is_indistinguishable_from_a_device_one(tmp_path: Path) -> None:
    """Nothing in the response or the log says which kind of code was used."""
    from pilot.core.bench.audit_log import AuditLog

    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    codes = _enroll_device(client)["confirmation"]["recovery_codes"]
    client.delete("/api/v1/auth/session")

    body = client.post("/api/v1/auth/session", json={"password": "secret", "otp": codes[0]}).get_json()

    assert body == {"authenticated": True, "scope": "bench"}

    issued = [
        entry
        for entry in AuditLog(Bench(tmp_path / "benches" / "current")).entries(entry_type="session")
        if entry.get("event") == "issued"
    ]
    assert issued[0]["via"] == "password"


def test_two_factor_status_reports_the_device_limit(tmp_path: Path) -> None:
    from admin.backend.internal.two_factor_authentication import MAX_ENROLLED_DEVICES

    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())

    assert client.get("/api/v1/auth/two-factor").get_json()["max_devices"] == MAX_ENROLLED_DEVICES


def test_enrolling_past_the_limit_is_rejected(tmp_path: Path) -> None:
    from admin.backend.internal.two_factor_authentication import (
        MAX_ENROLLED_DEVICES,
        TwoFactorAuthentication,
    )

    client = _client(tmp_path)
    client.set_cookie("sid", _session_token())
    # Seeded directly: enrolling all of them over HTTP would trip the rate limiter first.
    two_factor = TwoFactorAuthentication(Bench(tmp_path / "benches" / "current"))
    for index in range(MAX_ENROLLED_DEVICES):
        two_factor.start_enrollment(f"device {index}")

    response = client.post("/api/v1/auth/two-factor/enrollment", json={"name": "one too many"})

    assert response.status_code == 422
    assert str(MAX_ENROLLED_DEVICES) in response.get_json()["error"]["message"]


def test_stores_read_files_written_before_the_record_shape(tmp_path: Path) -> None:
    """A dropped revocation would silently make a revoked token valid again."""
    import json

    from admin.backend.internal.session import ActiveTokens, RevokedTokens

    _client(tmp_path)  # lays down the bench this reads through
    bench_root = tmp_path / "benches" / "current"
    expires = int(time.time()) + 3600
    # The old on-disk shape: a bare exp int rather than a record.
    (bench_root / RevokedTokens.FILENAME).write_text(json.dumps({"old-jti": expires}))
    (bench_root / ActiveTokens.FILENAME).write_text(json.dumps({"old-active": expires}))

    bench = Bench(bench_root)
    assert "old-jti" in RevokedTokens(bench)
    assert ActiveTokens(bench).all()["old-active"]["exp"] == expires


def test_a_legacy_revoked_token_is_still_rejected(tmp_path: Path) -> None:
    import json

    from admin.backend.internal.session import RevokedTokens, Session

    client = _client(tmp_path)
    bench_root = tmp_path / "benches" / "current"
    token, jti = Session(Bench(bench_root)).issue_session_token()
    path = bench_root / RevokedTokens.FILENAME
    path.write_text(json.dumps({jti: int(time.time()) + 3600}))

    client.set_cookie("sid", token)

    assert client.get("/api/v1/auth/two-factor").status_code == 401


def test_revoking_removes_the_jti_from_active(tmp_path: Path) -> None:
    """It used to linger in .active-jtis.json, hidden only by a read-time filter."""
    from admin.backend.internal.session import ActiveTokens, RevokedTokens, Session

    client = _client(tmp_path)
    bench = Bench(tmp_path / "benches" / "current")
    _, jti = Session(bench).issue_session_token()
    client.set_cookie("sid", _session_token())

    assert client.post(f"/api/v1/auth/sessions/revoke/{jti}").status_code == 204

    assert jti in RevokedTokens(bench)
    assert jti not in ActiveTokens(bench).all()


def test_revoking_all_clears_active(tmp_path: Path) -> None:
    from admin.backend.internal.session import ActiveTokens, RevokedTokens, Session

    bench_root = tmp_path / "benches" / "current"
    client = _client(tmp_path)
    bench = Bench(bench_root)
    token, caller_jti = Session(bench).issue_session_token()
    _, other_jti = Session(bench).issue_session_token()
    client.set_cookie("sid", token)

    assert client.post("/api/v1/auth/sessions/revoke/all").status_code == 200

    revoked = RevokedTokens(bench)
    assert other_jti in revoked and caller_jti in revoked
    # Only the freshly issued replacement for the caller survives.
    assert other_jti not in ActiveTokens(bench).all()
    assert caller_jti not in ActiveTokens(bench).all()


def test_discarding_an_unknown_jti_does_not_rewrite(tmp_path: Path) -> None:
    from admin.backend.internal.session import ActiveTokens, Session

    client = _client(tmp_path)
    bench = Bench(tmp_path / "benches" / "current")
    Session(bench).issue_session_token()
    path = tmp_path / "benches" / "current" / ActiveTokens.FILENAME
    before = path.stat().st_mtime_ns

    ActiveTokens(bench).discard("never-existed")

    assert path.stat().st_mtime_ns == before


def test_bootstrap_tells_the_ui_central_is_off_by_default(tmp_path: Path) -> None:
    """The dashboard hides its Central surfaces on a self-hosted bench, so it has
    to be told which one this is."""
    client = _signed_in_client(tmp_path)

    assert client.get("/api/v1/bootstrap").get_json()["central"] is False


def test_bootstrap_reports_central_when_it_is_managed(tmp_path: Path) -> None:
    from pilot.config.common import CommonConfig

    client = _signed_in_client(tmp_path)
    with CommonConfig.open(tmp_path / "benches") as common:
        # Enabled but not bootstrapped is the pending screen, which says nothing else.
        common.central.enabled = True
        common.central.bootstrapped = True

    assert client.get("/api/v1/bootstrap").get_json()["central"] is True
