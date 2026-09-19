"""Every /api/v1/sites/<name> route accepts any hostname the site answers to."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from pilot.config import BenchConfig


def _make_bench(tmp_path: Path) -> Path:
    bench_root = tmp_path / "benches" / "current"
    bench_root.mkdir(parents=True)
    (bench_root / "bench.toml").write_text(
        BenchConfig.from_flat(bench_root.name, {"admin_enabled": True, "admin_password": "secret"}).dumps()
    )
    return bench_root


def _make_site(bench_root: Path, name: str, **config) -> None:
    site_dir = bench_root / "sites" / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps(config))


def _client(bench_root: Path, token: str | None = None):
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", token or Session(Bench(bench_root)).issue_session_token()[0])
    return client


def _site_token(bench_root: Path, site: str) -> str:
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    return Session(Bench(bench_root)).issue_site_token(site)


def test_site_detail_accepts_a_custom_domain(tmp_path: Path) -> None:
    bench_root = _make_bench(tmp_path)
    _make_site(bench_root, "site.localhost", domains=["shop.example.com"])

    response = _client(bench_root).get("/api/v1/sites/shop.example.com")

    assert response.status_code == 200
    assert response.get_json()["name"] == "site.localhost"


def test_site_configuration_accepts_an_old_hostname(tmp_path: Path) -> None:
    bench_root = _make_bench(tmp_path)
    _make_site(bench_root, "new.localhost", domains=["old.localhost"], developer_mode=1)

    response = _client(bench_root).get("/api/v1/sites/old.localhost/configuration")

    assert response.status_code == 200
    assert response.get_json()["developer_mode"] == 1


def test_a_site_token_issued_before_a_rename_still_reaches_its_site(tmp_path: Path) -> None:
    bench_root = _make_bench(tmp_path)
    token = _site_token(bench_root, "old.localhost")
    _make_site(
        bench_root,
        "new.localhost",
        domains=["old.localhost"],
        pilot_auth_token=token,
    )
    client = _client(bench_root, token=token)

    assert client.get("/api/v1/sites/new.localhost/configuration").status_code == 200


def test_a_stale_site_token_does_not_follow_a_reassigned_hostname(tmp_path: Path) -> None:
    bench_root = _make_bench(tmp_path)
    old_token = _site_token(bench_root, "old.localhost")
    new_token = _site_token(bench_root, "new.localhost")
    _make_site(
        bench_root,
        "new.localhost",
        domains=["old.localhost"],
        pilot_auth_token=new_token,
    )
    client = _client(bench_root, token=old_token)

    assert client.get("/api/v1/sites/old.localhost/configuration").status_code == 403


def test_a_revoked_site_token_cannot_reach_a_recreated_site(tmp_path: Path) -> None:
    bench_root = _make_bench(tmp_path)
    token = _site_token(bench_root, "old.localhost")
    _make_site(bench_root, "old.localhost")
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    Session(Bench(bench_root)).revoke_token(token)
    client = _client(bench_root, token=token)

    assert client.get("/api/v1/sites/old.localhost/configuration").status_code == 401


def test_a_site_token_reaches_its_site_through_a_custom_domain(tmp_path: Path) -> None:
    bench_root = _make_bench(tmp_path)
    _make_site(bench_root, "site.localhost", domains=["shop.example.com"])
    client = _client(bench_root, token=_site_token(bench_root, "site.localhost"))

    assert client.get("/api/v1/sites/shop.example.com/configuration").status_code == 200


def test_a_site_token_still_cannot_reach_another_site(tmp_path: Path) -> None:
    bench_root = _make_bench(tmp_path)
    _make_site(bench_root, "one.localhost")
    _make_site(bench_root, "two.localhost")
    client = _client(bench_root, token=_site_token(bench_root, "one.localhost"))

    assert client.get("/api/v1/sites/two.localhost/configuration").status_code == 403


def test_an_unknown_hostname_is_still_not_found(tmp_path: Path) -> None:
    bench_root = _make_bench(tmp_path)
    _make_site(bench_root, "new.localhost")

    assert _client(bench_root).get("/api/v1/sites/missing.localhost/configuration").status_code == 404


def test_aliases_are_not_scanned_before_authentication(tmp_path: Path) -> None:
    from admin.backend.app import create_app
    from pilot.core.bench import Bench

    bench_root = _make_bench(tmp_path)
    app = create_app(bench_root)
    app.config["TESTING"] = True
    with patch.object(Bench, "resolve_site_name") as resolve:
        response = app.test_client().get("/api/v1/sites/shop.example.com/configuration")

    assert response.status_code == 401
    resolve.assert_not_called()


def test_alias_resolution_failure_reports_unavailable_configuration(tmp_path: Path) -> None:
    from pilot.core.bench import Bench

    bench_root = _make_bench(tmp_path)
    with patch.object(Bench, "resolve_site_name", side_effect=ValueError("broken config")):
        response = _client(bench_root).get("/api/v1/sites/shop.example.com/configuration")

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "configuration_unavailable"


def test_legacy_token_check_reports_corrupt_site_configuration(tmp_path: Path) -> None:
    bench_root = _make_bench(tmp_path)
    token = _site_token(bench_root, "old.localhost")
    site_dir = bench_root / "sites" / "new.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("not json")

    response = _client(bench_root, token=token).get(
        "/api/v1/sites/new.localhost/configuration"
    )

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "configuration_unavailable"
