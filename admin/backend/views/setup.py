from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from admin.backend.tasks.manager.task_reader import TaskReader
from admin.backend.tasks.manager.task_runner import TaskRunner
from bench_cli.config.bench_toml_builder import FRAMEWORK_BRANCHES, BenchTomlBuilder

setup_bp = Blueprint("setup", __name__)


@setup_bp.route("/config")
def get_config():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    return jsonify(_read_defaults(bench_root))


@setup_bp.route("/branches")
def get_branches():
    return jsonify({"branches": FRAMEWORK_BRANCHES})


@setup_bp.route("/save", methods=["POST"])
def save_config():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}

    error = _validate(data)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    # Preserve any settings the wizard didn't send (e.g. python version, fields
    # not shown in the current step). Incoming data wins on conflicts.
    toml_path = bench_root / "bench.toml"
    existing: dict = {}
    if toml_path.exists():
        try:
            existing = BenchTomlBuilder.read_settings(toml_path)
        except Exception:
            pass

    settings = {**existing, **data, "admin_enabled": True}
    content = BenchTomlBuilder(_current_name(bench_root), settings).render()
    toml_path.write_text(content)
    return jsonify({"ok": True})


def _validate(data: dict) -> str | None:
    for field in ("mariadb_password", "admin_password"):
        if not data.get(field):
            return f"{field} is required"
    if not data.get("volume_pool"):
        return "volume_pool is required"
    backing = data.get("volume_backing", "auto")
    if backing == "device" and not data.get("volume_device"):
        return "volume_device is required when volume backing is a block device"
    if backing == "image" and not data.get("volume_image_size"):
        return "volume_image_size is required when volume backing is a disk image"
    return None


@setup_bp.route("/init", methods=["POST"])
def start_init():
    from bench_cli.config.bench_config import BenchConfig
    from bench_cli.managers.volume_manager import VolumeManager

    bench_root = Path(current_app.config["BENCH_ROOT"])

    # Pre-flight validation so volume sizing errors surface in the wizard
    # instead of failing deep inside the init task.
    try:
        config = BenchConfig.from_file(bench_root / "bench.toml")
        config.validate()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if error := VolumeManager(config.volume).validate_sizes_fit_backing():
        return jsonify({"ok": False, "error": error}), 400

    try:
        task_id = TaskRunner(bench_root).run("bench-init", {})
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@setup_bp.route("/finish", methods=["POST"])
def finish_setup():
    """Shut down the standalone wizard server so the user can run `bench start`.

    Only the wizard server (started with --wizard) may be shut down this way —
    the procfile-managed admin process must never exit, since the dev-mode
    runner stops the whole bench when any one process dies.
    """
    import os
    import signal
    import threading

    if not current_app.config.get("WIZARD_SERVER"):
        return jsonify({"ok": False, "error": "Not running as the setup-wizard server"}), 400

    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not (bench_root / "config" / "Procfile").exists():
        return jsonify({"ok": False, "error": "Bench is not initialized yet"}), 400

    # call_on_close fires after the response body has been written to the
    # socket, so the kill can't race ahead of the response. The tiny timer
    # just lets the handler thread finish tearing down the connection.
    response = jsonify({"ok": True})
    response.call_on_close(lambda: threading.Timer(0.1, lambda: os.kill(os.getpid(), signal.SIGTERM)).start())
    return response


@setup_bp.route("/new-site", methods=["POST"])
def start_new_site():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return jsonify({"ok": False, "error": "Site name is required"}), 400

    args = {"name": data["name"]}
    if data.get("admin_password"):
        args["admin_password"] = data["admin_password"]
    try:
        task_id = TaskRunner(bench_root).run("new-site", args)
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@setup_bp.route("/stream/<task_id>")
def stream_task(task_id: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    reader = TaskReader(bench_root)

    def generate():
        for line in reader.stream_output(task_id):
            if line.startswith("__DONE__:"):
                yield f"event: done\ndata: {line[9:]}\n\n"
            else:
                yield f"data: {line}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


def _read_defaults(bench_root: Path) -> dict:
    from admin.backend.tasks.manager.task_reader import TaskReader
    from bench_cli.platform import is_linux

    result = {
        "bench_name": bench_root.name,
        "is_linux": is_linux(),
        **BenchTomlBuilder.DEFAULTS,
    }
    toml_path = bench_root / "bench.toml"
    if toml_path.exists():
        try:
            result.update(BenchTomlBuilder.read_settings(toml_path))
            if not result.get("bench_name"):
                result["bench_name"] = bench_root.name
        except Exception:
            pass

    result.update(_volume_suggestions(toml_path))

    try:
        tasks = TaskReader(bench_root).list_tasks()
        running = next((t for t in tasks if t.command == "bench-init" and t.status == "running"), None)
        result["running_init_task_id"] = running.task_id if running else None
    except Exception:
        result["running_init_task_id"] = None

    return result


def _volume_suggestions(toml_path: Path) -> dict:
    """Smart volume defaults for the wizard.

    Fresh setups (no [volume] table yet) get discovery-driven defaults:
    device backing on the largest unused disk, or image backing sized from
    rootfs free space, with quotas/reservations derived from the backing size.
    Existing volume config is never overridden — only the discovered device
    list is returned so the UI can still offer a dropdown.
    """
    from bench_cli.platform import is_linux

    if not is_linux():
        return {"available_devices": []}

    from bench_cli.managers.volume_manager import (
        compute_smart_defaults,
        list_device_choices,
        rootfs_free_bytes,
    )

    slider_bounds = {"rootfs_free_bytes": rootfs_free_bytes()}

    try:
        import tomllib

        with open(toml_path, "rb") as f:
            has_volume_config = "volume" in tomllib.load(f)
    except Exception:
        has_volume_config = False

    if has_volume_config:
        return {"available_devices": list_device_choices(), **slider_bounds}
    return {**compute_smart_defaults(), **slider_bounds}


def _current_name(bench_root: Path) -> str:
    toml_path = bench_root / "bench.toml"
    if not toml_path.exists():
        return bench_root.name
    try:
        return BenchTomlBuilder.read_settings(toml_path).get("bench_name") or bench_root.name
    except Exception:
        return bench_root.name
