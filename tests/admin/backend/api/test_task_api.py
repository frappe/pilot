from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from flask import Flask

from pilot.tasks.manager.task_state import TaskStatus
from pilot.tasks.manager.task_store import TaskStore
from pilot.tasks.manager.worker_state import WorkerIntent, WorkerStore
from admin.backend.api.v1.tasks import task_worker_bp, tasks_bp
from pilot.exceptions import TaskConflictError, TaskNotFoundError


TASK_ID = "20260715-120000-aabbcc"
RETRY_TASK_ID = "20260715-120100-ddeeff"


def client(bench_root: Path):
    app = Flask(__name__)
    app.config["BENCH_ROOT"] = bench_root
    app.register_blueprint(tasks_bp, url_prefix="/api/v1/tasks")
    app.register_blueprint(task_worker_bp, url_prefix="/api/v1")
    return app.test_client()


def create_task(
    bench_root: Path,
    task_id: str = TASK_ID,
    status: TaskStatus = TaskStatus.QUEUED,
) -> None:
    store = TaskStore(bench_root)
    store.create_queued(
        {
            "task_id": task_id,
            "command": "build",
            "args": {"app": "frappe"},
            "command_argv": ["bench", "build", "--app", "frappe"],
            "queued_at": "2026-07-15T12:00:00+00:00",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "failure": None,
            "bench_root": str(bench_root),
        }
    )
    if status != TaskStatus.QUEUED:
        store.transition(task_id, TaskStatus.QUEUED, TaskStatus.RUNNING)
    if status in {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.KILLED}:
        store.transition(
            task_id,
            TaskStatus.RUNNING,
            status,
            {
                "finished_at": "2026-07-15T12:00:02+00:00",
                "exit_code": 0 if status == TaskStatus.SUCCESS else 1,
            },
        )


def test_submit_returns_queued_task_and_location(tmp_path: Path) -> None:
    def submit(*args, **kwargs):
        create_task(tmp_path)
        return TASK_ID

    with patch("admin.backend.api.v1.tasks.TaskRunner.run", side_effect=submit) as run:
        response = client(tmp_path).post(
            "/api/v1/tasks",
            json={"command": "build", "app": "frappe"},
            headers={"Idempotency-Key": "client-request-key"},
        )

    assert response.status_code == 202
    assert response.headers["Location"] == f"/api/v1/tasks/{TASK_ID}"
    assert response.get_json()["task_id"] == TASK_ID
    assert response.get_json()["status"] == "queued"
    run.assert_called_once_with(
        "build",
        {"app": "frappe"},
        idempotency_key="client-request-key",
    )


def test_submit_returns_conflict_for_incompatible_idempotency_key(
    tmp_path: Path,
) -> None:
    with patch(
        "admin.backend.api.v1.tasks.TaskRunner.run",
        side_effect=TaskConflictError("Idempotency key conflict"),
    ):
        response = client(tmp_path).post(
            "/api/v1/tasks",
            json={"command": "build"},
            headers={"Idempotency-Key": "client-request-key"},
        )

    assert response.status_code == 409
    assert response.get_json()["error"]["message"] == "Idempotency key conflict"


def test_submit_rejects_malformed_json(tmp_path: Path) -> None:
    response = client(tmp_path).post(
        "/api/v1/tasks",
        data="not-json",
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "malformed_request"


def test_submit_rejects_invalid_command_field(tmp_path: Path) -> None:
    response = client(tmp_path).post("/api/v1/tasks", json={"command": ""})

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_task"


def test_list_rejects_unknown_status_filter(tmp_path: Path) -> None:
    response = client(tmp_path).get("/api/v1/tasks?status=unknown")

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_task_status"


def test_task_detail_is_a_resource_without_embedded_output(tmp_path: Path) -> None:
    create_task(tmp_path)

    response = client(tmp_path).get(f"/api/v1/tasks/{TASK_ID}")

    assert response.status_code == 200
    assert response.get_json()["task_id"] == TASK_ID
    assert "task" not in response.get_json()
    assert "output" not in response.get_json()


def test_task_detail_exposes_queue_position_and_safe_failure(tmp_path: Path) -> None:
    create_task(tmp_path)
    queued = client(tmp_path).get(f"/api/v1/tasks/{TASK_ID}").get_json()
    store = TaskStore(tmp_path)
    store.transition(
        TASK_ID,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        {"started_at": "2026-07-15T12:00:01+00:00"},
    )
    store.transition(
        TASK_ID,
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
        {
            "finished_at": "2026-07-15T12:00:02+00:00",
            "exit_code": 1,
            "failure": {"code": "unknown", "message": "secret text"},
        },
    )
    failed = client(tmp_path).get(f"/api/v1/tasks/{TASK_ID}").get_json()

    assert queued["queue_position"] == 1
    assert queued["failure"] is None
    assert failed["queue_position"] is None
    assert failed["failure"] == {
        "code": "command_failed",
        "message": "Task command failed.",
    }
    assert "secret text" not in str(failed)


def test_cancel_returns_no_content(tmp_path: Path) -> None:
    create_task(tmp_path)

    response = client(tmp_path).delete(f"/api/v1/tasks/{TASK_ID}")

    assert response.status_code == 204
    assert response.get_data() == b""
    assert TaskStore(tmp_path).read_status(TASK_ID) == TaskStatus.KILLED


def test_cancel_rejects_a_task_that_is_no_longer_active(tmp_path: Path) -> None:
    create_task(tmp_path, status=TaskStatus.SUCCESS)

    response = client(tmp_path).delete(f"/api/v1/tasks/{TASK_ID}")

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "task_not_active"


def test_retry_returns_queued_task_and_location(tmp_path: Path) -> None:
    create_task(tmp_path, status=TaskStatus.SUCCESS)

    def retry(*args, **kwargs):
        create_task(tmp_path, RETRY_TASK_ID)
        return RETRY_TASK_ID

    with patch("admin.backend.api.v1.tasks.TaskRunner.run", side_effect=retry):
        response = client(tmp_path).post(
            f"/api/v1/tasks/{TASK_ID}/actions/retry"
        )

    assert response.status_code == 202
    assert response.headers["Location"] == f"/api/v1/tasks/{RETRY_TASK_ID}"
    assert response.get_json()["task_id"] == RETRY_TASK_ID
    assert response.get_json()["status"] == "queued"


def test_retry_rejects_an_active_task(tmp_path: Path) -> None:
    create_task(tmp_path)

    response = client(tmp_path).post(f"/api/v1/tasks/{TASK_ID}/actions/retry")

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "task_not_finished"


def test_missing_task_is_rejected_before_sse_response(tmp_path: Path) -> None:
    with patch(
        "admin.backend.api.v1.tasks.TaskReader.read_task",
        side_effect=TaskNotFoundError(f"Task not found: {TASK_ID}"),
    ):
        response = client(tmp_path).get(f"/api/v1/tasks/{TASK_ID}/events")

    assert response.status_code == 404
    assert response.mimetype == "application/json"


def test_events_keep_status_updates_out_of_output_event_ids(tmp_path: Path) -> None:
    create_task(tmp_path)
    events = iter(
        [
            {"type": "status", "status": "queued", "queue_position": 1},
            {"type": "line", "line": "started"},
            {
                "type": "done",
                "status": "success",
                "exit_code": 0,
                "failure": None,
            },
        ]
    )
    with patch(
        "admin.backend.api.v1.tasks.TaskReader.stream_output",
        return_value=events,
    ):
        response = client(tmp_path).get(f"/api/v1/tasks/{TASK_ID}/events")

    blocks = response.get_data(as_text=True).strip().split("\n\n")
    assert blocks[0].startswith("data: ")
    assert "id:" not in blocks[0]
    assert blocks[1].startswith("id: 1\n")
    assert blocks[2].startswith("id: 2\n")


def test_output_content_is_a_download(tmp_path: Path) -> None:
    create_task(tmp_path)
    envelope = "<14>1 2026-07-15T12:00:00Z host build 123 - - "
    (tmp_path / "tasks" / TASK_ID / "output.log").write_text(
        f"{envelope}[50%]\r{envelope}[100%]\n{envelope}task output\n",
        encoding="utf-8",
    )

    response = client(tmp_path).get(f"/api/v1/tasks/{TASK_ID}/output/content")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "[100%]\ntask output\n"
    disposition = response.headers["Content-Disposition"]
    assert disposition.startswith("attachment;")
    assert f"{TASK_ID}_output.log" in disposition


def test_task_worker_status_is_a_resource(tmp_path: Path) -> None:
    response = client(tmp_path).get("/api/v1/task-worker")

    assert response.status_code == 200
    assert response.get_json() == {
        "active": False,
        "desired": "running",
        "queued_tasks": 0,
        "running_tasks": 0,
        "status": "not-started",
        "uncertain": False,
    }


def test_stop_task_worker_persists_intent_without_signalling_processes(
    tmp_path: Path,
) -> None:
    with (
        patch("admin.backend.api.v1.tasks.task_workers.wake", return_value=True) as wake,
        patch("admin.backend.api.v1.tasks.task_workers.start") as start,
        patch("admin.backend.api.v1.tasks.task_workers.request_drain") as drain,
    ):
        response = client(tmp_path).post("/api/v1/task-worker/actions/stop")

    assert response.status_code == 202
    assert response.headers["Location"] == "/api/v1/task-worker"
    assert response.get_json()["desired"] == "stopped"
    assert WorkerStore(tmp_path).read_intent() == WorkerIntent.STOPPED
    wake.assert_called_once_with(tmp_path)
    start.assert_not_called()
    drain.assert_not_called()


def test_start_task_worker_persists_intent_and_wakes_existing_thread(
    tmp_path: Path,
) -> None:
    WorkerStore(tmp_path).write_intent(WorkerIntent.STOPPED)
    with (
        patch("admin.backend.api.v1.tasks.task_workers.wake", return_value=True) as wake,
        patch("admin.backend.api.v1.tasks.task_workers.start") as start,
    ):
        response = client(tmp_path).post("/api/v1/task-worker/actions/start")

    assert response.status_code == 202
    assert response.headers["Location"] == "/api/v1/task-worker"
    assert response.get_json()["desired"] == "running"
    assert WorkerStore(tmp_path).read_intent() == WorkerIntent.RUNNING
    wake.assert_called_once_with(tmp_path)
    start.assert_not_called()
