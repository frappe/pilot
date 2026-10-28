"""Cloud Settings in-app embed must answer Desk's /embed/cloud-settings/ URL."""

from __future__ import annotations

from pathlib import Path

import pytest

from admin.backend import app as admin_app


def _client(tmp_path: Path):
    from admin.backend.app import create_app

    bench = tmp_path / "bench"
    bench.mkdir()
    (bench / "bench.toml").write_text('[bench]\nname = "bench"\n')
    flask_app = create_app(bench)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_in_app_embed_bundle_is_served_at_desk_contract_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    in_app_embed_dir = tmp_path / "in-app-embed" / "cloud-settings"
    in_app_embed_dir.mkdir(parents=True)
    (in_app_embed_dir / "cloud-settings.js").write_text("frappe.cloudSettings={show(){}}")
    monkeypatch.setattr(admin_app, "STATIC_DIR", tmp_path)

    response = _client(tmp_path).get("/embed/cloud-settings/cloud-settings.js")

    assert response.status_code == 200
    assert response.content_type.startswith("text/javascript")
    assert b"frappe.cloudSettings" in response.data
    # Fixed filename: it must revalidate so a rebuild is picked up, never pin the
    # old bytes with `immutable`. ETag lets revalidation settle for a cheap 304.
    cache_control = response.headers.get("Cache-Control", "")
    assert "no-cache" in cache_control
    assert "immutable" not in cache_control
    assert response.headers.get("ETag")


def test_in_app_embed_bundle_is_not_swallowed_by_dashboard_spa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dashboard = tmp_path / "dashboard"
    dashboard.mkdir()
    (dashboard / "index.html").write_text("<!doctype html><title>dashboard</title>")
    in_app_embed_dir = tmp_path / "in-app-embed" / "cloud-settings"
    in_app_embed_dir.mkdir(parents=True)
    (in_app_embed_dir / "cloud-settings.js").write_text("// in-app-embed")
    monkeypatch.setattr(admin_app, "STATIC_DIR", tmp_path)

    response = _client(tmp_path).get("/embed/cloud-settings/cloud-settings.js")

    assert response.status_code == 200
    assert b"dashboard" not in response.data
    assert response.data.startswith(b"// in-app-embed")


def test_missing_in_app_embed_build_returns_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin_app, "STATIC_DIR", tmp_path / "empty-static")
    (tmp_path / "empty-static").mkdir()

    response = _client(tmp_path).get("/embed/cloud-settings/cloud-settings.js")

    assert response.status_code == 503
    assert b"not built" in response.data


def test_sdk_runtime_is_served_with_cors_and_hashed_chunks_are_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    esm_dir = tmp_path / "in-app-embed" / "cloud-settings" / "esm"
    (esm_dir / "chunks").mkdir(parents=True)
    (esm_dir / "cloud-settings.js").write_text("export {}")
    (esm_dir / "chunks" / "BillingPanel-abc123.js").write_text("export {}")
    monkeypatch.setattr(admin_app, "STATIC_DIR", tmp_path)
    client = _client(tmp_path)

    entry = client.get("/embed/cloud-settings/esm/cloud-settings.js")
    chunk = client.get("/embed/cloud-settings/esm/chunks/BillingPanel-abc123.js")

    assert entry.headers["Access-Control-Allow-Origin"] == "*"
    assert entry.headers["Cache-Control"] == "no-cache"
    assert chunk.headers["Access-Control-Allow-Origin"] == "*"
    assert "immutable" in chunk.headers["Cache-Control"]
