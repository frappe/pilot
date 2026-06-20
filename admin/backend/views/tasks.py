from __future__ import annotations

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    request,
    stream_with_context,
)

from bench_cli.exceptions import TaskNotFoundError, TaskNotRunningError
from admin.backend.tasks.manager.task_reader import TaskReader
from admin.backend.tasks.manager.task_runner import TaskRunner

tasks_bp = Blueprint("tasks", __name__)


def _task_dict(t):
    return {
        "task_id": t.task_id,
        "command": t.command,
        "args": t.args,
        "status": t.status,
        "pid": t.pid,
        "started_at": t.started_at.isoformat(),
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        "exit_code": t.exit_code,
        "duration_seconds": t.duration_seconds,
    }


@tasks_bp.route("/")
def index():
    bench_root = current_app.config["BENCH_ROOT"]
    status_filter = request.args.get("status", "")

    try:
        task_list = TaskReader(bench_root).list_tasks()
    except Exception as error:
        return jsonify({"error": str(error)}), 500

    if status_filter and status_filter != "all":
        task_list = [t for t in task_list if t.status == status_filter]

    return jsonify([_task_dict(t) for t in task_list])


@tasks_bp.route("/<task_id>")
def task_detail(task_id: str):
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        reader = TaskReader(bench_root)
        task = reader.read_task(task_id)
        output = reader.read_output(task_id)
    except TaskNotFoundError as error:
        return jsonify({"error": str(error)}), 404
    except Exception as error:
        return jsonify({"error": str(error)}), 500

    return jsonify({"task": _task_dict(task), "output": output})


@tasks_bp.route("/<task_id>/stream")
def stream_task_output(task_id: str):
    bench_root = current_app.config["BENCH_ROOT"]
    reader = TaskReader(bench_root)

    def generate():
        for line in reader.stream_output(task_id):
            if line.startswith("__DONE__:"):
                yield f"event: done\ndata: {line[9:]}\n\n"
            elif line.startswith("__CR__:"):
                yield f"event: overwrite\ndata: {line[7:]}\n\n"
            else:
                yield f"data: {line}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@tasks_bp.route("/run", methods=["POST"])
def run_task():
    bench_root = current_app.config["BENCH_ROOT"]
    data = request.get_json(silent=True) or {}
    command = (data.pop("command", "") or "").strip()
    args = data

    try:
        task_id = TaskRunner(bench_root).run(command, args)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500

    return jsonify({"ok": True, "task_id": task_id})


@tasks_bp.route("/<task_id>/kill", methods=["POST"])
def kill_task(task_id: str):
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        TaskRunner(bench_root).kill(task_id)
    except (TaskNotFoundError, TaskNotRunningError) as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500

    return jsonify({"ok": True})


@tasks_bp.route("/<task_id>/rerun", methods=["POST"])
def rerun_task(task_id: str):
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        task = TaskReader(bench_root).read_task(task_id)
    except TaskNotFoundError as error:
        return jsonify({"ok": False, "error": str(error)}), 404
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500

    try:
        new_task_id = TaskRunner(bench_root).run(task.command, task.args)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 400
    except Exception as error:
        return jsonify({"ok": False, "error": str(error)}), 500

    return jsonify({"ok": True, "task_id": new_task_id})


@tasks_bp.route("/<task_id>/output/download")
def download_task_output(task_id: str):
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        task = TaskReader(bench_root).read_task(task_id)
    except TaskNotFoundError as error:
        return jsonify({"error": str(error)}), 404
    except Exception as error:
        return jsonify({"error": str(error)}), 500

    output_path = task.output_path
    if not output_path.exists():
        return jsonify({"error": "No output file found"}), 404

    return Response(
        output_path.read_bytes(),
        mimetype="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{task_id}_output.log"'},
    )
