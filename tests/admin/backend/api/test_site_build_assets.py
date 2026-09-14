"""Tests for site asset building: core logic, BuildTask, and /api/v1/sites/<name>/actions/build-assets."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from pilot.config import BenchConfig
from pilot.exceptions import BenchError


def _write_bench_toml(bench_dir: Path, name: str, **settings) -> None:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(BenchConfig.from_flat(name, settings).dumps())


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    _write_bench_toml(bench_root, bench_root.name, admin_enabled=True, admin_password=password)
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", Session(Bench(bench_root)).issue_session_token()[0])
    return client


def _site_client(bench_root: Path, site: str):
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    _write_bench_toml(bench_root, bench_root.name, admin_enabled=True, admin_password="secret")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", Session(Bench(bench_root)).issue_site_token(site))
    return client


def _unauthenticated_client(bench_root: Path):
    from admin.backend.app import create_app

    _write_bench_toml(bench_root, bench_root.name, admin_enabled=True, admin_password="secret")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    return app.test_client()


def _make_site(bench_root: Path, name: str, installed_apps: list[str]) -> None:
    site_dir = bench_root / "sites" / name
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site_config.json").write_text(json.dumps({"installed_apps": installed_apps}))


# ---------------------------------------------------------------------------
# Core logic: Site.build_assets() / SiteCommands.build_assets()
# ---------------------------------------------------------------------------


def test_site_build_assets_invokes_rebuild_assets(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["frappe", "erpnext"])
    from pilot.config import SiteConfig
    from pilot.core.site import Site

    bench = Mock()
    bench.sites_path = bench_root / "sites"
    site = Site(SiteConfig(name="site1.localhost", apps=["frappe", "erpnext"]), bench)

    with patch.object(site, "active_apps", return_value=["frappe", "erpnext"]):
        site.build_assets()

    bench.rebuild_assets.assert_called_once_with(apps=["frappe", "erpnext"], force=True)


def test_site_build_assets_with_specific_app(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["frappe", "erpnext"])
    from pilot.config import SiteConfig
    from pilot.core.site import Site

    bench = Mock()
    bench.sites_path = bench_root / "sites"
    site = Site(SiteConfig(name="site1.localhost", apps=["frappe", "erpnext"]), bench)

    with patch.object(site, "active_apps", return_value=["frappe", "erpnext"]):
        site.build_assets(app="erpnext")

    bench.rebuild_assets.assert_called_once_with(apps=["erpnext"], force=True)


def test_site_build_assets_empty_active_apps_is_noop(tmp_path: Path) -> None:
    """BUG-1: Empty active_apps() must not trigger a full bench-wide rebuild."""
    from pilot.config import SiteConfig
    from pilot.core.site import Site

    bench = Mock()
    bench.sites_path = tmp_path / "sites"
    site = Site(SiteConfig(name="empty.localhost", apps=[]), bench)

    with patch.object(site, "active_apps", return_value=[]):
        site.build_assets()

    bench.rebuild_assets.assert_not_called()


def test_site_build_assets_rejects_app_not_on_site(tmp_path: Path) -> None:
    """GAP-3: Building assets for an app not installed on the site raises BenchError."""
    from pilot.config import SiteConfig
    from pilot.core.site import Site

    bench = Mock()
    bench.sites_path = tmp_path / "sites"
    site = Site(SiteConfig(name="site1.localhost", apps=["frappe"]), bench)

    with (
        patch.object(site, "active_apps", return_value=["frappe"]),
        pytest.raises(BenchError, match="not installed"),
    ):
        site.build_assets(app="nonexistent_app")

    bench.rebuild_assets.assert_not_called()


def test_site_build_assets_wraps_active_apps_failure(tmp_path: Path) -> None:
    """MINOR-3: active_apps() failure raises a descriptive BenchError."""
    from pilot.config import SiteConfig
    from pilot.core.site import Site

    bench = Mock()
    bench.sites_path = tmp_path / "sites"
    site = Site(SiteConfig(name="broken.localhost", apps=[]), bench)

    with (
        patch.object(site, "active_apps", side_effect=RuntimeError("DB down")),
        pytest.raises(BenchError, match="Cannot determine installed apps"),
    ):
        site.build_assets()



# ---------------------------------------------------------------------------
# BuildTask
# ---------------------------------------------------------------------------


def test_build_task_label_with_site_and_app() -> None:
    from pilot.tasks.build import BuildTask, _build_step_label

    task = Mock(spec=BuildTask)
    task.site = "site1.localhost"
    task.app = None
    assert _build_step_label(task) == "Build assets for site1.localhost"

    task.app = "erpnext"
    assert _build_step_label(task) == "Build assets for erpnext on site1.localhost"


def test_build_task_label_bench_wide() -> None:
    from pilot.tasks.build import BuildTask, _build_step_label

    task = Mock(spec=BuildTask)
    task.site = None
    task.app = None
    assert _build_step_label(task) == "Build assets"

    task.app = "frappe"
    assert _build_step_label(task) == "Build assets for frappe"


def test_build_task_site_path_calls_site_build_assets() -> None:
    from pilot.tasks.build import BuildTask

    bench = Mock()
    site_mock = Mock()
    bench.site.return_value = site_mock

    build_task = BuildTask(bench=bench, bench_root=Path("/tmp"), site="site1.localhost", app="erpnext", force=True)
    with patch("pilot.tasks.build.step", lambda key, label: (lambda f: f)):
        build_task.build()

    bench.site.assert_called_once_with("site1.localhost")
    site_mock.build_assets.assert_called_once_with(app="erpnext", force=True)


def test_build_task_bench_path_calls_rebuild_assets() -> None:
    """BUG-2: Bench-wide build (no site) still delegates to bench.rebuild_assets."""
    from pilot.tasks.build import BuildTask

    bench = Mock()
    build_task = BuildTask(bench=bench, bench_root=Path("/tmp"))
    with patch("pilot.tasks.build.step", lambda key, label: (lambda f: f)):
        build_task.build()

    bench.rebuild_assets.assert_called_once_with(apps=None, force=False)


def test_build_task_force_defaults_to_false() -> None:
    """BUG-2: Default force=False preserves backward compatibility for CLI builds."""
    from pilot.tasks.build import BuildTask

    task = BuildTask(bench=Mock(), bench_root=Path("/tmp"))
    assert task.force is False


def test_build_task_bench_path_with_app() -> None:
    """Bench-wide build with a specific app."""
    from pilot.tasks.build import BuildTask

    bench = Mock()
    build_task = BuildTask(bench=bench, bench_root=Path("/tmp"), app="frappe")
    with patch("pilot.tasks.build.step", lambda key, label: (lambda f: f)):
        build_task.build()

    bench.rebuild_assets.assert_called_once_with(apps=["frappe"], force=False)


# ---------------------------------------------------------------------------
# API endpoint: /api/v1/sites/<name>/actions/build-assets
# ---------------------------------------------------------------------------


def test_api_build_site_assets_queues_task(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["frappe"])
    client = _client(bench_root)

    with patch("pilot.internal.tasks.runner.task_workers.wake", return_value=False):
        response = client.post("/api/v1/sites/site1.localhost/actions/build-assets")

    assert response.status_code == 202
    body = response.get_json()
    assert body["command"] == "build"
    assert body["args"]["site"] == "site1.localhost"
    assert body["args"]["force"] is True  # API default


def test_api_build_site_assets_with_app_param(tmp_path: Path) -> None:
    """GAP-1: The endpoint accepts an optional 'app' in the request body."""
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["frappe", "erpnext"])
    client = _client(bench_root)

    with patch("pilot.internal.tasks.runner.task_workers.wake", return_value=False):
        response = client.post(
            "/api/v1/sites/site1.localhost/actions/build-assets",
            json={"app": "erpnext"},
        )

    assert response.status_code == 202
    body = response.get_json()
    assert body["args"]["app"] == "erpnext"
    assert body["args"]["site"] == "site1.localhost"


def test_api_build_site_assets_with_force_false(tmp_path: Path) -> None:
    """GAP-1: The endpoint accepts an optional 'force' flag."""
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["frappe"])
    client = _client(bench_root)

    with patch("pilot.internal.tasks.runner.task_workers.wake", return_value=False):
        response = client.post(
            "/api/v1/sites/site1.localhost/actions/build-assets",
            json={"force": False},
        )

    assert response.status_code == 202
    body = response.get_json()
    assert body["args"]["force"] is False


def test_api_build_site_assets_rejects_invalid_app_field(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["frappe"])
    client = _client(bench_root)

    response = client.post(
        "/api/v1/sites/site1.localhost/actions/build-assets",
        json={"app": 123},
    )

    assert response.status_code == 422


def test_api_build_site_assets_rejects_invalid_force_field(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["frappe"])
    client = _client(bench_root)

    response = client.post(
        "/api/v1/sites/site1.localhost/actions/build-assets",
        json={"force": "yes"},
    )

    assert response.status_code == 422


def test_api_build_site_assets_rejects_missing_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = client.post("/api/v1/sites/missing.localhost/actions/build-assets")

    assert response.status_code == 404


def test_api_build_site_assets_works_with_site_token(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["frappe"])
    client = _site_client(bench_root, "site1.localhost")

    with patch("pilot.internal.tasks.runner.task_workers.wake", return_value=False):
        response = client.post("/api/v1/sites/site1.localhost/actions/build-assets")

    assert response.status_code == 202


def test_api_build_site_assets_rejects_cross_site_token(tmp_path: Path) -> None:
    """GAP-4: A site-scoped token for site1 must not be able to build assets for site2."""
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["frappe"])
    _make_site(bench_root, "site2.localhost", ["frappe"])
    client = _site_client(bench_root, "site1.localhost")

    response = client.post("/api/v1/sites/site2.localhost/actions/build-assets")

    assert response.status_code == 403


def test_api_build_site_assets_rejects_unauthenticated(tmp_path: Path) -> None:
    """GAP-5: A request with no auth token returns 401."""
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["frappe"])
    client = _unauthenticated_client(bench_root)

    response = client.post("/api/v1/sites/site1.localhost/actions/build-assets")

    assert response.status_code == 401
