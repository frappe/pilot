from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from flask import Flask

from admin.backend.views.tasks import tasks_bp
from pilot.exceptions import TaskConflictError


def client(bench_root: Path):
    app = Flask(__name__)
    app.config["BENCH_ROOT"] = bench_root
    app.register_blueprint(tasks_bp, url_prefix="/api/tasks")
    return app.test_client()


def test_run_forwards_idempotency_key(tmp_path: Path) -> None:
    with patch(
        "admin.backend.views.tasks.TaskRunner.run",
        return_value="20260715-120000-aabbcc",
    ) as run:
        response = client(tmp_path).post(
            "/api/tasks/run",
            json={"command": "build", "app": "frappe"},
            headers={"Idempotency-Key": "client-request-key"},
        )

    assert response.status_code == 200
    run.assert_called_once_with(
        "build",
        {"app": "frappe"},
        idempotency_key="client-request-key",
    )


def test_run_returns_conflict_for_incompatible_idempotency_key(tmp_path: Path) -> None:
    with patch(
        "admin.backend.views.tasks.TaskRunner.run",
        side_effect=TaskConflictError("Idempotency key conflict"),
    ):
        response = client(tmp_path).post(
            "/api/tasks/run",
            json={"command": "build"},
            headers={"Idempotency-Key": "client-request-key"},
        )

    assert response.status_code == 409
    assert response.get_json() == {
        "ok": False,
        "error": "Idempotency key conflict",
    }
