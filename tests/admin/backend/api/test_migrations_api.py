"""Tests for admin backend migrations API endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.core.bench.migration.state import get_state


def _write_bench_toml(bench_dir: Path, name: str, **settings) -> None:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(BenchConfig.from_flat(name, settings).dumps())


def _write_task(bench_root: Path, task_id: str, status: str | None) -> None:
    task_dir = bench_root / "tasks" / task_id
    task_dir.mkdir(parents=True)
    if status is not None:
        (task_dir / "status").write_text(status)


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


def test_migrations_list_and_current(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    (bench_root / "sites" / "site1.localhost").mkdir(parents=True)
    (bench_root / "sites" / "site1.localhost" / "site_config.json").write_text("{}")
    client = _client(bench_root)

    resp = client.get("/api/v1/migrations")
    assert resp.status_code == 200
    assert resp.get_json() == {"data": [], "meta": {"limit": 20, "next_cursor": None}}

    resp_curr = client.get("/api/v1/migrations/current")
    assert resp_curr.status_code == 200
    assert resp_curr.get_json() is None


def test_post_updates_creates_operation(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    (bench_root / "sites" / "site1.localhost").mkdir(parents=True)
    (bench_root / "sites" / "site1.localhost" / "site_config.json").write_text("{}")
    client = _client(bench_root)

    with (
        # Never read the machine's real marketplace cache from a test.
        patch("pilot.integrations.marketplace.Marketplace.registry", return_value=[]),
        patch("pilot.tasks.migration_backup.MigrationBackupTask.queue", return_value="task-99"),
    ):
        resp = client.post("/api/v1/updates", json={})

    assert resp.status_code == 202
    data = resp.get_json()
    assert "operation" in data
    op_id = data["operation"]["id"]
    assert data["task_id"] == "task-99"

    op_resp = client.get(f"/api/v1/migrations/{op_id}")
    assert op_resp.status_code == 200
    op_data = op_resp.get_json()
    assert op_data["kind"] == "update"
    assert op_data["state"] == "backing_up"
    assert "skip_failing_patches" not in op_data


def test_post_updates_rejects_when_one_is_already_unresolved(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    (bench_root / "sites" / "site1.localhost").mkdir(parents=True)
    (bench_root / "sites" / "site1.localhost" / "site_config.json").write_text("{}")
    client = _client(bench_root)

    with (
        patch("pilot.integrations.marketplace.Marketplace.registry", return_value=[]),
        patch("pilot.tasks.migration_backup.MigrationBackupTask.queue", return_value="task-99"),
    ):
        first = client.post("/api/v1/updates", json={})
        assert first.status_code == 202

        second = client.post("/api/v1/updates", json={})

    assert second.status_code == 409
    assert second.get_json()["error"]["code"] == "migration_conflict"


def test_standalone_migrate_runs_in_place_without_a_backup(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    site_dir = bench_root / "sites" / "site1.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{}")
    client = _client(bench_root)

    with (
        patch("pilot.integrations.marketplace.Marketplace.registry", return_value=[]),
        patch("pilot.tasks.migration_backup.MigrationBackupTask.queue") as backup,
        patch("pilot.tasks.migrate.MigrateTask.queue", return_value="task-99"),
    ):
        response = client.post("/api/v1/sites/site1.localhost/actions/migrate", json={})

    assert response.status_code == 202
    data = response.get_json()
    assert data["operation_id"]
    assert data["task_id"] == "task-99"
    assert response.headers["Location"].endswith(f"/migrations/{data['operation_id']}")
    backup.assert_not_called()
    assert Bench(bench_root).migrations.get(data["operation_id"]).can_revert is False


def test_restore_action_uses_documented_endpoint(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    site_dir = bench_root / "sites" / "site1.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{}")
    client = _client(bench_root)
    operation = Bench(bench_root).migrations.create_site_migrate("site1.localhost")
    operation.state = get_state("needs_attention")
    operation.sites[0].backup_status = "backed_up"
    operation.store.save(operation)

    with patch("pilot.tasks.revert_migration.RevertMigrationTask.queue", return_value="task-restore"):
        response = client.post(f"/api/v1/migrations/{operation.id}/actions/restore")

    assert response.status_code == 202
    assert response.get_json()["task_id"] == "task-restore"


def test_bypass_patch_rejects_a_stale_patch_identifier(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    site_dir = bench_root / "sites" / "site1.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{}")
    client = _client(bench_root)
    operation = Bench(bench_root).migrations.create_site_migrate("site1.localhost")
    operation.state = get_state("needs_attention")
    operation.failed_site = "site1.localhost"
    operation.diagnosis = {"patch": "frappe.patches.expected"}
    operation.store.save(operation)

    with patch("pilot.tasks.bypass_patch.BypassPatchTask.queue") as queue:
        response = client.post(
            f"/api/v1/migrations/{operation.id}/actions/bypass-patch",
            json={"patch": "frappe.patches.stale"},
        )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "patch_mismatch"
    queue.assert_not_called()


def test_migration_detail_includes_retained_task_logs(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    site_dir = bench_root / "sites" / "site1.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{}")
    client = _client(bench_root)
    operation = Bench(bench_root).migrations.create_site_migrate("site1.localhost")
    backup = "20260101-000001-aaaaaa"
    first_try = "20260101-000002-bbbbbb"
    second_try = "20260101-000003-cccccc"
    operation.chain = [
        {"command": "migration-backup", "task_id": backup, "site": "site1.localhost"},
        {"command": "migrate", "task_id": first_try, "site": "site1.localhost"},
        {"command": "migrate", "task_id": second_try, "site": "site1.localhost"},
    ]
    operation.store.save(operation)
    for task_id, status in ((backup, "success"), (first_try, "failed"), (second_try, "running")):
        _write_task(bench_root, task_id, status)

    response = client.get(f"/api/v1/migrations/{operation.id}")

    assert response.status_code == 200
    data = response.get_json()
    # Status is what tells the UI which attempt of a retried step was the one that broke.
    assert data["task_logs"] == [
        {"id": backup, "label": "Backup", "site": "site1.localhost", "status": "success"},
        {"id": first_try, "label": "Migrate", "site": "site1.localhost", "status": "failed"},
        {
            "id": second_try,
            "label": "Migrate (attempt 2)",
            "site": "site1.localhost",
            "status": "running",
        },
    ]
    # Pruned task logs are omitted; the operation stays readable.
    assert all("tasks" not in site for site in data["sites"])


def test_task_logs_report_no_status_for_a_half_pruned_task(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    site_dir = bench_root / "sites" / "site1.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{}")
    client = _client(bench_root)
    operation = Bench(bench_root).migrations.create_site_migrate("site1.localhost")
    task_id = "20260101-000004-dddddd"
    operation.chain = [{"command": "migrate", "task_id": task_id, "site": "site1.localhost"}]
    operation.store.save(operation)
    _write_task(bench_root, task_id, status=None)

    response = client.get(f"/api/v1/migrations/{operation.id}")

    assert response.status_code == 200
    # The entry still links to its log; only the verdict is missing.
    assert response.get_json()["task_logs"] == [
        {"id": task_id, "label": "Migrate", "site": "site1.localhost", "status": None}
    ]


def test_detail_reports_a_queued_action_while_the_operation_is_paused(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    site_dir = bench_root / "sites" / "site1.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{}")
    client = _client(bench_root)
    operation = Bench(bench_root).migrations.create_site_migrate("site1.localhost")
    operation.state = get_state("needs_attention")
    operation.task_ids = {"retry": "20260101-000000-abc123"}
    operation.store.save(operation)
    task_dir = bench_root / "tasks" / "20260101-000000-abc123"
    task_dir.mkdir(parents=True)
    (task_dir / "meta.json").write_text(
        json.dumps(
            {
                "task_id": "20260101-000000-abc123",
                "command": "retry-update",
                "args": {},
                "queued_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )
    (task_dir / "status").write_text("queued")

    pending = client.get(f"/api/v1/migrations/{operation.id}").get_json()["pending_action"]
    assert pending["role"] == "retry"
    assert pending["task_id"] == "20260101-000000-abc123"
    assert pending["status"] == "queued"

    # Once the worker is done, the operation state alone drives the UI again.
    (task_dir / "status").write_text("success")
    assert client.get(f"/api/v1/migrations/{operation.id}").get_json()["pending_action"] is None
