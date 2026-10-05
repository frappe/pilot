"""The dashboard SPA route serves the build and nothing outside it."""

from __future__ import annotations

from pathlib import Path

import pytest

from admin.backend import app as admin_app

# Each of these served bench.toml before serve_frontend used send_from_directory.
TRAVERSALS = (
    "/../../bench/bench.toml",
    "/..%2f..%2fbench%2fbench.toml",
    "/%2e%2e/%2e%2e/bench/bench.toml",
    "/assets/../../../bench/bench.toml",
)


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from admin.backend.app import create_app

    bench = tmp_path / "bench"
    bench.mkdir()
    (bench / "bench.toml").write_text('[admin]\npassword = "top-secret"\njwt_secret = "signing-key"\n')
    dashboard = tmp_path / "static" / "dashboard"
    (dashboard / "assets").mkdir(parents=True)
    (dashboard / "index.html").write_text("<!doctype html><title>dashboard</title>")
    (dashboard / "assets" / "app.js").write_text("// app")
    monkeypatch.setattr(admin_app, "STATIC_DIR", tmp_path / "static")
    flask_app = create_app(bench)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


@pytest.mark.parametrize("path", TRAVERSALS)
def test_dashboard_route_never_serves_a_file_outside_the_build(
    path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This route is unauthenticated, so a readable bench.toml here is a forged session."""
    response = _client(tmp_path, monkeypatch).get(path)

    assert b"top-secret" not in response.data
    assert b"signing-key" not in response.data


def test_dashboard_route_serves_the_build_and_falls_back_to_the_spa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    asset = client.get("/assets/app.js")
    unknown_route = client.get("/sites/site1.localhost")

    assert asset.status_code == 200
    assert asset.data == b"// app"
    assert "immutable" in asset.headers.get("Cache-Control", "")
    assert unknown_route.status_code == 200
    assert b"dashboard" in unknown_route.data


def test_a_chunk_removed_by_a_rebuild_is_a_404_not_the_spa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """index.html served as a module script hides the real failure."""
    response = _client(tmp_path, monkeypatch).get("/assets/TaskDetail-old.js")

    assert response.status_code == 404
