"""Tests for admin backend migrations API endpoints."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.core.bench.migration.state import get_state


def _write_bench_toml(bench_dir: Path, name: str, **settings) -> None:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(BenchConfig.from_flat(name, settings).dumps())


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from admin.backend.auth import ensure_jwt_secret, issue_token

    _write_bench_toml(bench_root, bench_root.name, admin_enabled=True, admin_password=password)
    secret = ensure_jwt_secret(bench_root / "bench.toml")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))
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

    with patch("pilot.tasks.migration_backup.MigrationBackupTask.queue", return_value="task-99"):
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

    with patch("pilot.tasks.migration_backup.MigrationBackupTask.queue", return_value="task-99"):
        first = client.post("/api/v1/updates", json={})
        assert first.status_code == 202

        second = client.post("/api/v1/updates", json={})

    assert second.status_code == 409
    assert second.get_json()["error"]["code"] == "migration_conflict"


def test_standalone_migrate_returns_operation_and_task_ids(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    site_dir = bench_root / "sites" / "site1.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{}")
    client = _client(bench_root)

    with patch("pilot.tasks.migration_backup.MigrationBackupTask.queue", return_value="task-99"):
        response = client.post("/api/v1/sites/site1.localhost/actions/migrate", json={})

    assert response.status_code == 202
    data = response.get_json()
    assert data["operation_id"]
    assert data["task_id"] == "task-99"
    assert response.headers["Location"].endswith(f"/migrations/{data['operation_id']}")


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
    operation.chain = [
        {"command": "migration-backup", "task_id": "backup-1", "site": "site1.localhost"},
        {"command": "migrate", "task_id": "migrate-1", "site": "site1.localhost"},
        {"command": "migrate", "task_id": "migrate-2", "site": "site1.localhost"},
    ]
    operation.store.save(operation)
    for task_id in ("backup-1", "migrate-1", "migrate-2"):
        (bench_root / "tasks" / task_id).mkdir(parents=True)

    response = client.get(f"/api/v1/migrations/{operation.id}")

    assert response.status_code == 200
    data = response.get_json()
    assert data["task_logs"] == [
        {"id": "backup-1", "label": "Backup", "site": "site1.localhost"},
        {"id": "migrate-1", "label": "Migrate", "site": "site1.localhost"},
        {"id": "migrate-2", "label": "Migrate (attempt 2)", "site": "site1.localhost"},
    ]
    # Pruned task logs are omitted; the operation stays readable.
    assert all("tasks" not in site for site in data["sites"])
