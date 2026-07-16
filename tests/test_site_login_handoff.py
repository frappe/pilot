from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from admin.backend.site_login_handoff import SiteLoginHandoffStore
from tests.test_admin_app import _client


def _write_site(bench_root: Path, name: str = "s.localhost", **config) -> None:
    site_path = bench_root / "sites" / name
    site_path.mkdir(parents=True)
    (site_path / "site_config.json").write_text(json.dumps(config))


def test_handoff_store_consumes_token_once() -> None:
    now = [1000.0]
    store = SiteLoginHandoffStore(clock=lambda: now[0])

    issued = store.issue("s.localhost", "http://s.localhost:8000/desk", secure=False)

    assert store.consume(issued.token, "s.localhost") == issued.handoff
    assert store.consume(issued.token, "s.localhost") is None


def test_handoff_store_rejects_expired_and_wrong_host_tokens() -> None:
    now = [1000.0]
    store = SiteLoginHandoffStore(clock=lambda: now[0], ttl_seconds=60)
    wrong_host = store.issue("s.localhost", "http://s.localhost:8000/desk", secure=False)
    expired = store.issue("s.localhost", "http://s.localhost:8000/desk", secure=False)

    assert store.consume(wrong_host.token, "other.localhost") is None
    now[0] = 1061.0
    assert store.consume(expired.token, "s.localhost") is None


def test_create_site_login_link_contains_no_secret_in_url(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write_site(bench_root)

    response = client.post("/api/v1/sites/s.localhost/login-links")

    body = response.get_json()
    assert response.status_code == 201
    assert response.headers["Location"] == body["url"]
    assert response.headers["Cache-Control"] == "no-store"
    assert body["method"] == "POST"
    assert body["url"] == "http://s.localhost:7000/api/v1/site-login-handoffs"
    assert body["handoff_token"] not in body["url"]
    assert "sid=" not in json.dumps(body)


def test_site_login_handoff_sets_cookie_and_cannot_be_replayed(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write_site(bench_root)
    link = client.post("/api/v1/sites/s.localhost/login-links").get_json()

    with patch(
        "admin.backend.api.v1.site_login.create_site_session",
        return_value="frappe-session",
    ) as create_session:
        response = client.post(
            link["url"],
            data={"handoff_token": link["handoff_token"]},
        )
        replay = client.post(
            link["url"],
            data={"handoff_token": link["handoff_token"]},
        )

    assert response.status_code == 303
    assert response.headers["Location"] == "http://s.localhost:8000/desk"
    assert "sid=" not in response.headers["Location"]
    assert response.headers["Cache-Control"] == "no-store"
    cookie = response.headers["Set-Cookie"]
    assert "sid=frappe-session" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Secure" not in cookie
    assert link["handoff_token"] not in str(response.headers)
    assert replay.status_code == 401
    create_session.assert_called_once_with(bench_root, "s.localhost")


def test_site_login_link_rejects_missing_and_symlinked_sites(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    outside = tmp_path / "outside"
    _write_site(outside, "linked.localhost")
    sites = bench_root / "sites"
    sites.mkdir()
    (sites / "linked.localhost").symlink_to(
        outside / "sites" / "linked.localhost",
        target_is_directory=True,
    )

    missing = client.post("/api/v1/sites/missing.localhost/login-links")
    linked = client.post("/api/v1/sites/linked.localhost/login-links")

    assert missing.status_code == linked.status_code == 404
