from __future__ import annotations

from collections.abc import Iterator

from flask import (
    Blueprint,
    Response,
    jsonify,
    request,
    stream_with_context,
    url_for,
)

from admin.backend.api.requests import ApiRequest
from admin.backend.api.responses import (
    accepted_response,
    accepted_task_response,
    error_response,
    no_content_response,
)
from admin.backend.draining import is_draining
from pilot.exceptions import (
    TaskConflictError,
    TaskNotCancellableError,
    TaskNotFoundError,
    TaskNotRunningError,
)
from pilot.managers.task import (
    TaskActivityReader,
    TaskReader,
    TaskStatus,
    TaskWorkerControl,
    sse_message,
    task_has_secrets,
)
from pilot.tasks import TaskRunner

tasks_bp = Blueprint("tasks", __name__)
task_worker_bp = Blueprint("task_worker", __name__)


@tasks_bp.get("")
def list_tasks():
    """List the tasks."""
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
    limit_arg = request.args.get("limit", "50")
    try:
        limit = int(limit_arg)
    except ValueError:
        limit = 0
    if limit < 1:
        return error_response(
            "invalid_task_limit",
            f"The limit parameter must be a positive integer, got {limit_arg!r}.",
            422,
        )
    try:
        tasks = reader.list_tasks(limit=limit)
    except Exception:
        return error_response("task_list_unavailable", "Could not read tasks.", 500)
    if wanted_status is not None:
        tasks = [task for task in tasks if task.status == wanted_status]
    return jsonify([task.as_dict() for task in tasks])


@tasks_bp.post("")
def create_task():
    """Queue a task."""
    data = ApiRequest.get_json_body()
    command = data.get("command")
    if not isinstance(command, str) or not command.strip():
        return error_response(
            "invalid_task",
            "The command field must be a non-empty string.",
            422,
        )
    args = {key: value for key, value in data.items() if key != "command"}
    try:
        task_id = TaskRunner(ApiRequest.get_bench_root()).run(
            command.strip(),
            args,
            idempotency_key=ApiRequest.get_idempotency_key(),
        )
        return accepted_task_response(ApiRequest.get_bench_root(), task_id)
    except TaskConflictError as error:
        return error_response("task_conflict", str(error), 409)
    except ValueError as error:
        return error_response("invalid_task", str(error), 422)
    except Exception:
        return error_response("task_creation_failed", "Could not create task.", 500)


@tasks_bp.get("/<task_id>")
def get_task(task_id: str):
    """Show one task."""
    try:
        return jsonify(_reader().read_task(task_id).as_dict())
    except TaskNotFoundError as error:
        return error_response("task_not_found", str(error), 404)
    except Exception:
        return error_response("task_unavailable", "Could not read task.", 500)


@tasks_bp.delete("/<task_id>")
def cancel_task(task_id: str):
    """Cancel a task."""
    try:
        TaskRunner(ApiRequest.get_bench_root()).kill(task_id)
    except TaskNotFoundError as error:
        return error_response("task_not_found", str(error), 404)
    except TaskNotRunningError as error:
        return error_response("task_not_active", str(error), 409)
    except TaskNotCancellableError as error:
        return error_response("task_not_cancellable", str(error), 409)
    except Exception:
        return error_response("task_cancellation_failed", "Could not cancel task.", 500)
    return no_content_response()


@tasks_bp.post("/<task_id>/actions/retry")
def retry_task(task_id: str):
    """Queue the task again with its arguments."""
    try:
        task = _reader().read_task(task_id)
    except TaskNotFoundError as error:
        return error_response("task_not_found", str(error), 404)
    except Exception:
        return error_response("task_unavailable", "Could not read task.", 500)
    if task.status.is_active:
        return error_response(
            "task_not_finished",
            "An active task cannot be retried.",
            409,
        )
    if task.command == "new-site":
        return _retry_new_site(task)
    if task_has_secrets(task.command, task.args):
        return error_response(
            "fresh_credentials_required",
            "This task requires fresh credentials and cannot be retried.",
            409,
        )
    try:
        bench_root = ApiRequest.get_bench_root()
        return accepted_task_response(
            bench_root,
            TaskRunner(bench_root).run(task.command, task.args),
        )
    except ValueError as error:
        return error_response("invalid_task", str(error), 422)
    except Exception:
        return error_response("task_creation_failed", "Could not retry task.", 500)


def _retry_new_site(task):
    """Recreate a failed site using its recorded app selection and fresh credentials."""
    from admin.backend.api.v1.sites.core import start_site_creation
    from pilot.core.site.creation import SiteCreationOwner
    from pilot.internal.validators import validate_site_name

    if task.status != TaskStatus.FAILED:
        return error_response("task_not_failed", "Only failed site creation can be retried.", 409)
    name = task.args.get("name")
    apps = task.args.get("apps", [])
    if (
        not isinstance(name, str)
        or validate_site_name(name)
        or not isinstance(apps, list)
        or not all(isinstance(app, str) for app in apps)
    ):
        return error_response("invalid_task", "The original site settings are unavailable.", 422)

    bench_root = _bench_root()
    owner = SiteCreationOwner(bench_root, name)
    with owner.locked():
        if owner.path.exists() or (bench_root / "sites" / name).exists():
            return error_response(
                "site_cleanup_incomplete",
                "The previous attempt still has site files or cleanup in progress. "
                "Inspect the failed task before trying again.",
                409,
            )
        return start_site_creation(bench_root, name, apps)


@tasks_bp.get("/<task_id>/events")
def task_events(task_id: str):
    """Stream the task events."""
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

    return Response(
        stream_with_context(_task_event_messages(reader, task_id, skip)),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


def _task_event_messages(reader: TaskReader, task_id: str, skip: int) -> Iterator[str]:
    """SSE messages, without the output events the client already has."""
    event_id = 0
    for event in reader.stream_output(task_id, should_stop=is_draining):
        if event is None:
            yield ": heartbeat\n\n"
            continue
        if event["type"] == "status":
            yield sse_message(event)
            continue
        event_id += 1
        if event_id > skip:
            yield sse_message(event, event_id)


@tasks_bp.get("/<task_id>/output/content")
def task_output(task_id: str):
    """Show the task output."""
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
        headers={"Content-Disposition": f'attachment; filename="{task_id}_output.log"'},
    )


@tasks_bp.get("/<task_id>/debug")
def debug_task(task_id: str):
    """Stream an AI explanation of why a failed task failed (SSE)."""
    reader = _reader()
    try:
        task = reader.read_task(task_id)
    except TaskNotFoundError as error:
        return error_response("task_not_found", str(error), 404)
    except Exception:
        return error_response("task_unavailable", "Could not read task.", 500)
    if task.status != TaskStatus.FAILED:
        return error_response("task_not_failed", "Only failed tasks can be debugged.", 409)

    from pilot.config import BenchConfig
    from pilot.integrations.llm.base import LLMError
    from pilot.integrations.llm.registry import is_configured
    from pilot.managers.task.debug import stream_task_debug

    bench_root = ApiRequest.get_bench_root()
    config = BenchConfig.read(bench_root)
    if not is_configured(config.llm):
        return error_response("ai_not_configured", "Connect an AI assistant in Settings first.", 409)

    output = "".join(reader.iter_output(task_id)) if task.output_path.exists() else ""
    refresh = request.args.get("refresh") == "1"

    def generate():
        """Yield the LLM debug text as events."""
        try:
            for text in stream_task_debug(
                config.llm,
                task,
                output,
                bench_root=bench_root,
                refresh=refresh,
            ):
                yield sse_message({"type": "delta", "text": text})
            yield sse_message({"type": "done"})
        except LLMError as error:
            yield sse_message({"type": "error", "message": str(error)})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@task_worker_bp.get("/task-worker")
def get_task_worker():
    """Show the task worker."""
    return jsonify(_worker_resource())


@task_worker_bp.post("/task-worker/actions/start")
def start_task_worker():
    """Ask the task worker to start."""
    bench_root = ApiRequest.get_bench_root()
    TaskWorkerControl(bench_root).request_start()
    return _accepted_worker()


@task_worker_bp.post("/task-worker/actions/stop")
def stop_task_worker():
    """Ask the task worker to stop."""
    bench_root = ApiRequest.get_bench_root()
    TaskWorkerControl(bench_root).request_stop()
    return _accepted_worker()


def _accepted_worker():
    """A 202 response with the task worker."""
    return accepted_response(
        _worker_resource(),
        url_for("task_worker.get_task_worker"),
    )


def _worker_resource() -> dict:
    """The task worker for the API."""
    activity = TaskActivityReader(ApiRequest.get_bench_root()).read()
    return {
        **activity.public_dict,
        "queued_tasks": activity.queued_tasks,
        "running_tasks": activity.running_tasks,
    }


def _reader() -> TaskReader:
    """The task reader."""
    return TaskReader(ApiRequest.get_bench_root())
