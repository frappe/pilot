from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tests.admin.backend.test_admin_app import _client


def _write_site(bench_root: Path, name: str = "s.localhost", **config) -> None:
    site_path = bench_root / "sites" / name
    site_path.mkdir(parents=True)
    (site_path / "site_config.json").write_text(json.dumps(config))


def test_create_login_link_returns_url_with_sid(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write_site(bench_root)

    with patch(
        "admin.backend.api.v1.sites.core.create_site_session",
        return_value="frappe-session-id",
    ) as create_session:
        response = client.post("/api/v1/sites/s.localhost/login")

    assert response.status_code == 201
    body = response.get_json()
    assert response.headers["Location"] == body["url"]
    assert response.headers["Cache-Control"] == "no-store"
    assert body["url"] == "http://s.localhost:8000/desk?sid=frappe-session-id"
    create_session.assert_called_once_with(bench_root, "s.localhost")


def test_create_login_link_fails_when_session_creation_fails(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write_site(bench_root)

    with patch(
        "admin.backend.api.v1.sites.core.create_site_session",
        return_value=None,
    ):
        response = client.post("/api/v1/sites/s.localhost/login")

    assert response.status_code == 503


def test_login_link_rejects_missing_and_symlinked_sites(tmp_path: Path) -> None:
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

    missing = client.post("/api/v1/sites/missing.localhost/login")
    linked = client.post("/api/v1/sites/linked.localhost/login")

    assert missing.status_code == linked.status_code == 404
