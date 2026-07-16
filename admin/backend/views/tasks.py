from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    request,
    stream_with_context,
    url_for,
)

from admin.backend.api_contract import accepted_response, error_response, no_content_response
from pilot.tasks.manager.activity import TaskActivityReader
from pilot.tasks.manager.events import sse_message
from pilot.tasks.manager.task_args import task_requires_secrets
from pilot.tasks.manager.task_reader import TaskReader
from pilot.tasks.manager.task_runner import TaskRunner
from pilot.tasks.manager.task_state import ACTIVE_TASK_STATUSES, TaskStatus
from pilot.tasks.manager.worker_registry import task_workers
from pilot.tasks.manager.worker_state import WorkerIntent, WorkerStore
from admin.backend.task_response import accepted_task_response
from pilot.exceptions import TaskConflictError, TaskNotFoundError, TaskNotRunningError

tasks_bp = Blueprint("tasks", __name__)
task_worker_bp = Blueprint("task_worker", __name__)


@tasks_bp.get("")
def list_tasks():
    reader = _reader()
    status_filter = request.args.get("status", "")
    wanted_status = None
    if status_filter and status_filter != "all":
        try:
            wanted_status = TaskStatus(status_filter)
        except ValueError:
            return error_response(
                "invalid_task_status",
                f"Unknown task status: {status_filter!r}.",
                422,
            )
    try:
        tasks = reader.list_tasks()
    except Exception:
        return error_response("task_list_unavailable", "Could not read tasks.", 500)
    if wanted_status is not None:
        tasks = [task for task in tasks if task.status == wanted_status]
    return jsonify([task.as_dict() for task in tasks])


@tasks_bp.post("")
def create_task():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    command = data.get("command")
    if not isinstance(command, str) or not command.strip():
        return error_response(
            "invalid_task",
            "The command field must be a non-empty string.",
            422,
        )
    args = {key: value for key, value in data.items() if key != "command"}
    try:
        task_id = TaskRunner(_bench_root()).run(
            command.strip(),
            args,
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
        return accepted_task_response(_bench_root(), task_id)
    except TaskConflictError as error:
        return error_response("task_conflict", str(error), 409)
    except ValueError as error:
        return error_response("invalid_task", str(error), 422)
    except Exception:
        return error_response("task_creation_failed", "Could not create task.", 500)


@tasks_bp.get("/<task_id>")
def get_task(task_id: str):
    try:
        return jsonify(_reader().read_task(task_id).as_dict())
    except TaskNotFoundError as error:
        return error_response("task_not_found", str(error), 404)
    except Exception:
        return error_response("task_unavailable", "Could not read task.", 500)


@tasks_bp.delete("/<task_id>")
def cancel_task(task_id: str):
    try:
        TaskRunner(_bench_root()).kill(task_id)
    except TaskNotFoundError as error:
        return error_response("task_not_found", str(error), 404)
    except TaskNotRunningError as error:
        return error_response("task_not_active", str(error), 409)
    except Exception:
        return error_response("task_cancellation_failed", "Could not cancel task.", 500)
    return no_content_response()


@tasks_bp.post("/<task_id>/actions/retry")
def retry_task(task_id: str):
    try:
        task = _reader().read_task(task_id)
    except TaskNotFoundError as error:
        return error_response("task_not_found", str(error), 404)
    except Exception:
        return error_response("task_unavailable", "Could not read task.", 500)
    if task_requires_secrets(task.command):
        return error_response(
            "fresh_credentials_required",
            "This task requires fresh credentials and cannot be retried.",
            409,
        )
    if task.status in ACTIVE_TASK_STATUSES:
        return error_response(
            "task_not_finished",
            "An active task cannot be retried.",
            409,
        )
    try:
        bench_root = _bench_root()
        return accepted_task_response(
            bench_root,
            TaskRunner(bench_root).run(task.command, task.args),
        )
    except ValueError as error:
        return error_response("invalid_task", str(error), 422)
    except Exception:
        return error_response("task_creation_failed", "Could not retry task.", 500)


@tasks_bp.get("/<task_id>/events")
def task_events(task_id: str):
    reader = _reader()
    try:
        reader.read_task(task_id)
    except TaskNotFoundError as error:
        return error_response("task_not_found", str(error), 404)
    except Exception:
        return error_response("task_unavailable", "Could not read task.", 500)

    try:
        skip = max(0, int(request.headers.get("Last-Event-ID", 0)))
    except ValueError:
        skip = 0

    def generate():
        event_id = 0
        for event in reader.stream_output(task_id):
            if event["type"] == "status":
                yield sse_message(event)
                continue
            event_id += 1
            if event_id > skip:
                yield sse_message(event, event_id)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@tasks_bp.get("/<task_id>/output/content")
def task_output(task_id: str):
    reader = _reader()
    try:
        task = reader.read_task(task_id)
    except TaskNotFoundError as error:
        return error_response("task_not_found", str(error), 404)
    except Exception:
        return error_response("task_unavailable", "Could not read task.", 500)
    if not task.output_path.exists():
        return error_response("task_output_not_found", "Task output is not available.", 404)
    return Response(
        stream_with_context(reader.iter_output(task_id)),
        mimetype="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="{task_id}_output.log"'
        },
    )


@task_worker_bp.get("/task-worker")
def get_task_worker():
    return jsonify(_worker_resource())


@task_worker_bp.post("/task-worker/actions/start")
def start_task_worker():
    bench_root = _bench_root()
    WorkerStore(bench_root).write_intent(WorkerIntent.RUNNING)
    task_workers.wake(bench_root)
    return _accepted_worker()


@task_worker_bp.post("/task-worker/actions/stop")
def stop_task_worker():
    bench_root = _bench_root()
    WorkerStore(bench_root).write_intent(WorkerIntent.STOPPED)
    task_workers.wake(bench_root)
    return _accepted_worker()


def _accepted_worker():
    return accepted_response(
        _worker_resource(),
        url_for("task_worker.get_task_worker"),
    )


def _worker_resource() -> dict:
    activity = TaskActivityReader(_bench_root()).read()
    return {
        **activity.public_dict(),
        "queued_tasks": activity.queued_tasks,
        "running_tasks": activity.running_tasks,
    }


def _reader() -> TaskReader:
    return TaskReader(_bench_root())


def _bench_root() -> Path:
    return Path(current_app.config["BENCH_ROOT"])
