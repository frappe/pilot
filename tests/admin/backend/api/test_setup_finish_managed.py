from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from admin.backend.app import create_app
from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.internal.tasks.store import TaskStore
from pilot.managers.task.models import TaskStatus


def _setup_client(bench_root: Path):
    from admin.backend.internal.session import Session

    bench_root.mkdir(parents=True, exist_ok=True)
    BenchConfig.write_flat(
        bench_root,
        bench_root.name,
        {"admin_enabled": True, "admin_password": "admin-secret"},
    )
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", Session(Bench(bench_root)).issue_session_token()[0])
    return client


def _start_and_complete_setup(client, bench_root: Path) -> str:
    assert client.put(
        "/api/v1/setup/configuration",
        json={"mariadb_password": "database-secret"},
    ).status_code == 200

    with patch("pilot.internal.tasks.runner.task_workers.wake"):
        response = client.post(
            "/api/v1/setup/actions/start",
            headers={"Idempotency-Key": "managed-process-setup"},
        )
    assert response.status_code == 202
    task_id = response.get_json()["task_id"]

    store = TaskStore(bench_root)
    store.transition(
        task_id,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        {"started_at": "2026-09-07T09:00:00+00:00"},
    )
    store.transition(
        task_id,
        TaskStatus.RUNNING,
        TaskStatus.SUCCESS,
        {
            "finished_at": "2026-09-07T09:01:00+00:00",
            "exit_code": 0,
        },
    )
    return task_id


def test_finish_accepts_initialized_bench_without_procfile(tmp_path: Path) -> None:
    """Managed process managers do not use config/Procfile.

    A successful wizard setup should finish when the bench Python environment
    exists even when no Procfile was generated.
    """
    client = _setup_client(tmp_path)
    task_id = _start_and_complete_setup(client, tmp_path)

    python = Bench(tmp_path).python
    python.parent.mkdir(parents=True, exist_ok=True)
    python.touch()

    assert not (tmp_path / "config" / "Procfile").exists()

    response = client.post(
        "/api/v1/setup/actions/finish",
        json={"task_id": task_id},
    )

    assert response.status_code == 204
    assert response.data == b""
    assert not (tmp_path / ".wizard-active").exists()


def test_finish_still_rejects_bench_without_python_environment(tmp_path: Path) -> None:
    client = _setup_client(tmp_path)
    task_id = _start_and_complete_setup(client, tmp_path)

    response = client.post(
        "/api/v1/setup/actions/finish",
        json={"task_id": task_id},
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "setup_not_initialized"
    assert (tmp_path / ".wizard-active").exists()
