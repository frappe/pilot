from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, current_app, jsonify

from admin.backend.api.responses import error_response
from admin.backend.api.v1.setup import wizard_marker_path
from admin.backend.middleware import allow_unauthenticated, is_request_authenticated
from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.internal.atomic_file import exclusive_file_lock
from pilot.managers.platform import native_process_manager
from pilot.managers.task import TaskActivityReader

core_bp = Blueprint("core", __name__)


@core_bp.get("/health")
@allow_unauthenticated
def health():
    response = jsonify({"status": "ok"})
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@core_bp.get("/bootstrap")
@allow_unauthenticated
def bootstrap():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        config = BenchConfig.read(bench_root)
    except Exception:
        if not BenchConfig.exists(bench_root):
            return jsonify(_setup_bootstrap(bench_root))
        return error_response(
            "configuration_unavailable",
            "Bench configuration is unavailable.",
            503,
        )

    if config.central.is_awaiting_bootstrap:
        return jsonify({"mode": "pending", "name": config.name, "enabled": True})

    initialized = (bench_root / "env" / "bin" / "python").exists()
    if not initialized or not config.admin.password:
        return jsonify(_setup_bootstrap(bench_root))

    marker = wizard_marker_path(bench_root)
    if marker.exists():
        with exclusive_file_lock(marker):
            if marker.exists():
                handoff_task_id = marker.read_text(encoding="utf-8").strip()
                if not handoff_task_id and _is_setup_complete(bench_root, config):
                    marker.unlink(missing_ok=True)
                else:
                    return jsonify(_setup_bootstrap(bench_root))
    return jsonify(
        _visible_bootstrap(
            bench_root,
            {
                "mode": "admin",
                "enabled": config.admin.enabled,
                "name": config.name,
                "db_type": config.db_type,
                "production": config.production.enabled,
                "native_process_manager": native_process_manager(),
                "allow_bench_management": config.admin.allow_bench_management,
                "central": config.central.enabled,
                "developer_mode": config.allow_developer_mode,
                "task_worker": TaskActivityReader(bench_root).read().public_dict,
            },
        )
    )


def _visible_bootstrap(bench_root: Path, payload: dict) -> dict:
    """Everything for a caller with a session; only what the sign-in page needs without."""
    if is_request_authenticated(Bench(bench_root)):
        return payload
    return {key: payload[key] for key in ("mode", "enabled", "name")}


def _setup_bootstrap(bench_root: Path) -> dict:
    name = bench_root.name
    try:
        name = BenchConfig.read(bench_root, validate=False).name or name
    except Exception as exc:
        logging.debug("Could not read bench name during setup bootstrap: %s", exc)
    return {"mode": "setup", "name": name, "enabled": True}


def _is_setup_complete(bench_root: Path, config: BenchConfig) -> bool:
    if not (bench_root / "env" / "bin" / "python").exists():
        return False
    if not config.admin.password:
        return False
    if config.production.process_manager and not config.production.enabled:
        return False
    try:
        from pilot.managers.task import TaskReader

        tasks = TaskReader(bench_root).list_tasks(limit=20)
        return not any(task.command == "wizard-setup" and task.status.is_active for task in tasks)
    except Exception:
        return True
