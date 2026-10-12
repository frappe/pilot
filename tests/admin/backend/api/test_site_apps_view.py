"""Tests for /api/v1/sites/<name>/apps: listing, install, and uninstall."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pilot.config import BenchConfig
from pilot.internal.tasks.store import TaskStore


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
    """A client holding only that site's token, as a managed site does."""
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    _write_bench_toml(bench_root, bench_root.name, admin_enabled=True, admin_password="secret")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", Session(Bench(bench_root)).issue_site_token(site))
    return client


def _make_site(bench_root: Path, name: str, installed_apps: list[str]) -> None:
    site_dir = bench_root / "sites" / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({"installed_apps": installed_apps}))


def _make_app(bench_root: Path, name: str, pyproject: str) -> None:
    app_dir = bench_root / "apps" / name
    app_dir.mkdir(parents=True)
    (app_dir / "pyproject.toml").write_text(pyproject)


def _make_cloned_app(bench_root: Path, name: str) -> None:
    app_dir = bench_root / "apps" / name
    app_dir.mkdir(parents=True)
    (app_dir / ".git").mkdir()


def test_site_apps_includes_title_and_description(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["suite"])
    _make_app(bench_root, "suite", '[project]\nname = "suite"\ndescription = "A custom suite app"\n')

    client = _client(bench_root)
    response = client.get("/api/v1/sites/site1.localhost/apps")

    assert response.status_code == 200
    apps = {app["name"]: app for app in response.get_json()["apps"]}
    assert apps["suite"]["title"] == "suite"
    assert apps["suite"]["description"] == "A custom suite app"


def test_site_apps_falls_back_to_name_when_app_missing(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["ghost"])

    client = _client(bench_root)
    response = client.get("/api/v1/sites/site1.localhost/apps")

    assert response.status_code == 200
    apps = {app["name"]: app for app in response.get_json()["apps"]}
    assert apps["ghost"]["title"] == "ghost"
    assert apps["ghost"]["description"] == ""


def test_site_marketplace_returns_catalog_for_existing_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    client = _client(bench_root)
    marketplace = Mock()
    marketplace.read_all_apps.return_value = [Mock(to_dict=lambda: {"app": "suite"})]

    with patch("pilot.integrations.marketplace.Marketplace", return_value=marketplace):
        response = client.get("/api/v1/sites/site1.localhost/marketplace")

    assert response.status_code == 200
    assert response.get_json() == [{"app": "suite"}]


def test_site_marketplace_rejects_missing_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = client.get("/api/v1/sites/missing.localhost/marketplace")

    assert response.status_code == 404


def test_site_task_returns_task_owned_by_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    task = Mock(args={"site": "site1.localhost"})
    task.as_dict.return_value = {"id": "task-1", "status": "running"}

    with patch(
        "admin.backend.api.v1.sites.apps.TaskReader.read_task",
        return_value=task,
    ):
        response = client.get("/api/v1/sites/site1.localhost/tasks/task-1")

    assert response.status_code == 200
    assert response.get_json() == {"id": "task-1", "status": "running"}


def test_site_task_rejects_task_owned_by_another_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    task = Mock(args={"site": "other.localhost"})

    with patch(
        "admin.backend.api.v1.sites.apps.TaskReader.read_task",
        return_value=task,
    ):
        response = client.get("/api/v1/sites/site1.localhost/tasks/task-1")

    assert response.status_code == 403


def test_site_task_tracks_full_update_operation(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    task = Mock(args={"operation_id": "update-1"})
    task.as_dict.return_value = {
        "task_id": "task-1",
        "status": "success",
        "exit_code": 0,
    }
    operation = Mock(
        state="updating",
        sites=[SimpleNamespace(name="site1.localhost")],
    )
    bench = Mock()
    bench.migrations.get.return_value = operation

    with (
        patch(
            "admin.backend.api.v1.sites.apps.TaskReader.read_task",
            return_value=task,
        ),
        patch("admin.backend.api.v1.sites.apps.Bench", return_value=bench),
    ):
        response = client.get("/api/v1/sites/site1.localhost/tasks/task-1")

    assert response.status_code == 200
    assert response.get_json()["status"] == "running"
    assert response.get_json()["exit_code"] is None


def test_site_task_reports_completed_update_operation(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    task = Mock(args={"operation_id": "update-1"})
    task.as_dict.return_value = {
        "task_id": "task-1",
        "status": "success",
        "exit_code": 0,
    }
    operation = Mock(
        state="completed",
        sites=[SimpleNamespace(name="site1.localhost")],
    )
    bench = Mock()
    bench.migrations.get.return_value = operation

    with (
        patch(
            "admin.backend.api.v1.sites.apps.TaskReader.read_task",
            return_value=task,
        ),
        patch("admin.backend.api.v1.sites.apps.Bench", return_value=bench),
    ):
        response = client.get("/api/v1/sites/site1.localhost/tasks/task-1")

    assert response.status_code == 200
    assert response.get_json()["status"] == "success"


def _post_install(client, site: str, **payload):
    with patch(
        "pilot.internal.tasks.runner.task_workers.wake",
        return_value=False,
    ):
        return client.post(f"/api/v1/sites/{site}/apps", json=payload)


def _delete_app(client, site: str, app: str, **query):
    with patch(
        "pilot.internal.tasks.runner.task_workers.wake",
        return_value=False,
    ):
        return client.delete(f"/api/v1/sites/{site}/apps/{app}", query_string=query)


def _post_update(client, site: str, payload: dict | None = None):
    return client.post(f"/api/v1/sites/{site}/actions/update-apps", json=payload)


def test_update_site_apps_starts_scoped_migration(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["frappe", "builder"])
    client = _client(bench_root)
    bench = Mock()
    operation = Mock(id="update-1")
    operation.begin.return_value = "task-1"
    bench.migrations.create_update.return_value = operation

    with patch("admin.backend.api.v1.sites.apps.Bench", return_value=bench):
        response = _post_update(client, "site1.localhost", {"apps": ["builder"]})

    assert response.status_code == 202
    assert response.get_json() == {"operation_id": "update-1", "task_id": "task-1"}
    bench.migrations.create_update.assert_called_once_with({"builder"})
    operation.begin.assert_called_once_with()
    assert response.headers["Location"].endswith("/api/v1/sites/site1.localhost/tasks/task-1")


def test_update_all_is_limited_to_apps_installed_on_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["frappe", "builder", "crm"])
    client = _client(bench_root)
    bench = Mock()
    operation = Mock(id="update-1")
    operation.begin.return_value = "task-1"
    bench.migrations.create_update.return_value = operation

    with patch("admin.backend.api.v1.sites.apps.Bench", return_value=bench):
        response = _post_update(client, "site1.localhost")

    assert response.status_code == 202
    bench.migrations.create_update.assert_called_once_with({"builder", "crm"})


def test_update_site_apps_rejects_app_not_installed_on_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["frappe", "builder"])
    client = _client(bench_root)

    response = _post_update(client, "site1.localhost", {"apps": ["erpnext"]})

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "app_not_installed"


def test_install_app_uses_fast_path_when_already_cloned(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    _make_cloned_app(bench_root, "suite")
    client = _client(bench_root)

    response = _post_install(client, "site1.localhost", app="suite")

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "install-app"
    assert body["args"] == {"site": "site1.localhost", "app": "suite"}


def test_install_app_fetches_by_repo_when_not_cloned(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    client = _client(bench_root)

    response = _post_install(
        client,
        "site1.localhost",
        app="suite",
        repo="https://github.com/frappe/suite",
        branch="develop",
    )

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "get-and-install-app"
    assert body["args"]["repo"] == "https://github.com/frappe/suite"
    assert body["args"]["branch"] == "develop"


def test_install_app_uses_fast_path_when_repo_points_at_a_cloned_app(tmp_path: Path) -> None:
    """Re-cloning an app the bench already has is wasted work - a repo URL for a
    cloned app must install straight onto the site."""
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    _make_cloned_app(bench_root, "suite")
    client = _client(bench_root)

    response = _post_install(client, "site1.localhost", repo="https://github.com/frappe/suite.git")

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "install-app"
    assert body["args"] == {"site": "site1.localhost", "app": "suite"}


def test_install_app_does_not_resolve_repo_before_queueing(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    client = _client(bench_root)

    response = _post_install(
        client,
        "site1.localhost",
        app="blog",
        repo="https://github.com/frappe/blog",
        branch="",
    )

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "get-and-install-app"
    assert body["args"]["repo"] == "https://github.com/frappe/blog"
    assert body["args"]["site"] == "site1.localhost"


def test_install_app_treats_bare_name_as_marketplace_when_not_cloned(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    client = _client(bench_root)

    response = _post_install(client, "site1.localhost", app="suite")

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "get-and-install-app"
    assert body["args"]["marketplace_app"] == "suite"


def test_install_app_from_a_repo_needs_a_bench_session(tmp_path: Path) -> None:
    """A clone lands in the bench every site shares, so one site's token must not
    choose the code it runs."""
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    client = _site_client(bench_root, "site1.localhost")

    response = _post_install(client, "site1.localhost", app="suite", repo="https://github.com/attacker/suite")

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "bench_scope_required"


def test_install_app_by_name_still_works_for_a_site_session(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    _make_cloned_app(bench_root, "suite")
    client = _site_client(bench_root, "site1.localhost")

    response = _post_install(client, "site1.localhost", app="suite")

    assert response.status_code == 202


def test_install_app_requires_app_or_repo(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    client = _client(bench_root)

    response = _post_install(client, "site1.localhost")

    assert response.status_code == 422


def test_install_app_rejects_missing_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = _post_install(client, "missing.localhost", app="suite")

    assert response.status_code == 404


def test_delete_site_app_queues_uninstall(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["suite"])
    client = _client(bench_root)

    response = _delete_app(client, "site1.localhost", "suite")

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "uninstall-app"
    assert body["args"] == {"site": "site1.localhost", "app": "suite", "force": False}


def test_delete_site_app_passes_force_flag(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["suite"])
    client = _client(bench_root)

    response = _delete_app(client, "site1.localhost", "suite", force="true")

    body = response.get_json()
    assert body["args"]["force"] is True


def test_delete_site_app_rejects_invalid_app_name(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    client = _client(bench_root)

    response = _delete_app(client, "site1.localhost", "bad.app")

    assert response.status_code == 422


def test_delete_site_app_rejects_missing_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = _delete_app(client, "missing.localhost", "suite")

    assert response.status_code == 404


def test_install_app_queues_the_importable_module_name(tmp_path: Path) -> None:
    """A repo folder named india-compliance holds the module india_compliance;
    install-app must be queued with the name frappe can actually import."""
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    app_dir = bench_root / "apps" / "india-compliance"
    (app_dir / "india_compliance").mkdir(parents=True)
    (app_dir / ".git").mkdir()
    (app_dir / "india_compliance" / "hooks.py").write_text("")
    client = _client(bench_root)

    response = _post_install(client, "site1.localhost", repo="https://github.com/frappe/india-compliance")

    body = response.get_json()
    assert response.status_code == 202
    assert body["args"] == {"site": "site1.localhost", "app": "india_compliance"}


def test_install_app_enables_it_inline_when_the_site_has_it_disabled(tmp_path: Path) -> None:
    """A disabled app is already on the site - enabling is a flag flip, not a task."""
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    _make_cloned_app(bench_root, "suite")
    client = _client(bench_root)

    with (
        patch("pilot.core.site.Site.disabled_apps", return_value=["suite"]),
        patch("pilot.core.site.Site.enable_app") as enable,
    ):
        response = _post_install(client, "site1.localhost", app="suite")

    assert response.status_code == 200
    assert response.get_json() == {"app": "suite", "enabled": True}
    assert enable.call_args.args[0].config.name == "suite"


def test_install_app_cannot_enable_inline_during_cli_site_drop(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    _make_cloned_app(bench_root, "suite")
    client = _client(bench_root)

    with (
        patch("pilot.core.site.Site.disabled_apps", return_value=["suite"]),
        patch("pilot.core.site.Site.enable_app") as enable,
        TaskStore(bench_root).reserve_direct_site("site1.localhost"),
    ):
        response = _post_install(client, "site1.localhost", app="suite")

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "task_conflict"
    enable.assert_not_called()


def test_delete_site_app_disables_inline_when_asked(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["suite"])
    _make_cloned_app(bench_root, "suite")
    client = _client(bench_root)

    with patch("pilot.core.site.Site.disable_app") as disable:
        response = _delete_app(client, "site1.localhost", "suite", mode="disable")

    assert response.status_code == 200
    assert response.get_json() == {"app": "suite", "disabled": True}
    assert disable.call_args.args[0].config.name == "suite"


def test_delete_site_app_cannot_disable_inline_during_cli_site_drop(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["suite"])
    _make_cloned_app(bench_root, "suite")
    client = _client(bench_root)

    with (
        patch("pilot.core.site.Site.disable_app") as disable,
        TaskStore(bench_root).reserve_direct_site("site1.localhost"),
    ):
        response = _delete_app(client, "site1.localhost", "suite", mode="disable")

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "task_conflict"
    disable.assert_not_called()
