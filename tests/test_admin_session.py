from __future__ import annotations

import time
import tomllib
from pathlib import Path

import pytest

from pilot.commands.generate_session import decode_token, has_scope, issue_login_token, issue_site_token, issue_token, verify_token
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
    from pilot.config.toml_writer import bench_config_to_toml

    bench_dir.mkdir(parents=True, exist_ok=True)
    toml_path = bench_dir / "bench.toml"
    toml_path.write_text(BenchTomlBuilder(bench_dir.name, {"admin_enabled": True, "admin_password": password}).render())
    config = BenchConfig.from_file(toml_path)
    config.admin.jwt_secret = jwt_secret
    toml_path.write_text(bench_config_to_toml(config))
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
    assert client.get("/api/status").get_json()["authenticated"] is True
    assert client.get("/api/benches/").status_code != 401


def test_invalid_jwt_cookie_stays_unauthenticated(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.set_cookie("sid", issue_token("wrong-secret"))
    assert client.get("/api/status").get_json()["authenticated"] is False
    assert client.get("/api/benches/").status_code == 401


def test_status_reports_bench_db_type(tmp_path: Path) -> None:
    # The engine is a bench-wide property; the admin reads it from /api/status to
    # show one bench-level badge instead of a per-site one.
    client = _client(tmp_path)
    assert client.get("/api/status").get_json()["db_type"] == "mariadb"


def test_status_reports_postgres_engine(tmp_path: Path) -> None:
    from admin.backend.app import create_app
    from pilot.config.toml_writer import bench_config_to_toml

    bench_root = tmp_path / "benches" / "pg"
    _initialized_bench(bench_root, "secret", "k3y")
    toml_path = bench_root / "bench.toml"
    config = BenchConfig.from_file(toml_path)
    config.db_type = "postgres"
    toml_path.write_text(bench_config_to_toml(config))

    app = create_app(bench_root)
    app.config["TESTING"] = True
    assert app.test_client().get("/api/status").get_json()["db_type"] == "postgres"


def test_status_reports_allow_bench_management_default_true(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/status").get_json()["allow_bench_management"] is True


def test_status_reports_allow_bench_management_when_disabled(tmp_path: Path) -> None:
    from admin.backend.app import create_app
    from pilot.config.toml_writer import bench_config_to_toml

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    toml_path = bench_root / "bench.toml"
    config = BenchConfig.from_file(toml_path)
    config.admin.allow_bench_management = False
    toml_path.write_text(bench_config_to_toml(config))

    app = create_app(bench_root)
    app.config["TESTING"] = True
    assert app.test_client().get("/api/status").get_json()["allow_bench_management"] is False


def test_login_with_sid_sets_httponly_cookie(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/login", json={"sid": issue_login_token("k3y")})
    assert resp.status_code == 200
    cookie = next(h for k, h in resp.headers if k == "Set-Cookie" and h.startswith("sid="))
    assert "HttpOnly" in cookie
    assert client.get("/api/benches/").status_code != 401


def test_login_with_invalid_sid_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/login", json={"sid": issue_login_token("wrong-secret")})
    assert resp.status_code == 401
    assert client.get("/api/benches/").status_code == 401


def test_sid_is_single_use(tmp_path: Path) -> None:
    client = _client(tmp_path)
    sid = issue_login_token("k3y")
    assert client.post("/api/login", json={"sid": sid}).status_code == 200
    assert client.post("/api/login", json={"sid": sid}).status_code == 401


def test_login_rate_limited_after_limit(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for _ in range(5):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 429


def test_setup_endpoint_requires_auth_once_password_set(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/api/setup/validate-mariadb", json={}).status_code == 401
    client.set_cookie("sid", issue_token("k3y"))
    assert client.post("/api/setup/validate-mariadb", json={}).status_code != 401


def test_setup_endpoint_open_before_password_set(tmp_path: Path) -> None:
    from admin.backend.app import create_app

    app = create_app(tmp_path)  # no bench.toml → first-time setup
    app.config["TESTING"] = True
    assert app.test_client().post("/api/setup/validate-mariadb", json={}).status_code != 401


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


def test_require_scope_allows_unscoped_token(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/test-scoped")
    @require_scope("example.com")
    def scoped_view():
        return jsonify({"ok": True})

    client = app.test_client()
    client.set_cookie("sid", issue_token("k3y"))
    assert client.get("/api/test-scoped").status_code == 200


def test_require_scope_allows_matching_scoped_token(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/test-scoped")
    @require_scope("example.com")
    def scoped_view():
        return jsonify({"ok": True})

    client = app.test_client()
    client.set_cookie("sid", issue_token("k3y", scope="site", site="example.com"))
    assert client.get("/api/test-scoped").status_code == 200


def test_require_scope_rejects_mismatched_scoped_token(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/test-scoped")
    @require_scope("example.com")
    def scoped_view():
        return jsonify({"ok": True})

    client = app.test_client()
    client.set_cookie("sid", issue_token("k3y", scope="site", site="other.com"))
    assert client.get("/api/test-scoped").status_code == 403


def test_current_site_scope_returns_site_from_claims(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import current_site_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/test-scope")
    def scope_view():
        return jsonify({"site": current_site_scope()})

    client = app.test_client()
    client.set_cookie("sid", issue_token("k3y", scope="site", site="example.com"))
    assert client.get("/api/test-scope").get_json()["site"] == "example.com"


def test_current_site_scope_returns_none_for_unscoped(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import current_site_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/test-scope")
    def scope_view():
        return jsonify({"site": current_site_scope()})

    client = app.test_client()
    client.set_cookie("sid", issue_token("k3y"))
    assert client.get("/api/test-scope").get_json()["site"] is None


def test_bearer_token_authenticates(tmp_path: Path) -> None:
    client = _client(tmp_path)
    token = issue_token("k3y")
    resp = client.get("/api/benches/", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code != 401


def test_bearer_token_with_site_scope(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/test-scoped")
    @require_scope("example.com")
    def scoped_view():
        return jsonify({"ok": True})

    client = app.test_client()
    token = issue_site_token("k3y", "example.com")
    resp = client.get("/api/test-scoped", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_bearer_token_wrong_site_rejected(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/test-scoped")
    @require_scope("example.com")
    def scoped_view():
        return jsonify({"ok": True})

    client = app.test_client()
    token = issue_site_token("k3y", "other.com")
    resp = client.get("/api/test-scoped", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_require_scope_with_callable(tmp_path: Path) -> None:
    from flask import jsonify
    from admin.backend.app import create_app
    from admin.backend.auth import require_scope

    bench_root = tmp_path / "benches" / "current"
    _initialized_bench(bench_root, "secret", "k3y")
    app = create_app(bench_root)
    app.config["TESTING"] = True

    @app.route("/api/sites/<name>/action")
    @require_scope(lambda kw: kw["name"])
    def scoped_view(name):
        return jsonify({"ok": True, "site": name})

    client = app.test_client()
    client.set_cookie("sid", issue_token("k3y", scope="site", site="example.com"))
    assert client.get("/api/sites/example.com/action").status_code == 200
    assert client.get("/api/sites/other.com/action").status_code == 403
