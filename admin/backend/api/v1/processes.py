from __future__ import annotations

import time
from pathlib import Path

import psutil
from flask import Blueprint, current_app, jsonify

from admin.backend.api.responses import error_response
from pilot.core.app.requirements import AppRequirementsError
from pilot.core.bench import Bench
from pilot.managers.processes.definitions import ProcessDefinitionBuilder
from pilot.managers.processes.local import ProcessManager

processes_bp = Blueprint("processes", __name__)


def _bench() -> Bench:
    return Bench(Path(current_app.config["BENCH_ROOT"]))


def _manager() -> ProcessManager:
    return ProcessManager.detect_running(_bench())


def _stats(pid: int | None) -> dict:
    if not pid:
        return {"cpu_percent": None, "memory_mb": None, "uptime": None}
    try:
        proc = psutil.Process(pid)
        proc.cpu_percent()  # first call is always 0.0; warms up the delta
        uptime_seconds = max(time.time() - proc.create_time(), 0)
        return {
            "cpu_percent": proc.cpu_percent(),
            "memory_mb": proc.memory_info().rss / 1024**2,
            "uptime": _format_uptime(uptime_seconds),
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {"cpu_percent": None, "memory_mb": None, "uptime": None}


def _format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _row(name: str, states: dict[str, dict], declaration: dict | None = None) -> dict:
    # Supervisor dashes underscores in program names; match on both keys.
    live = states.get(name) or states.get(name.replace("_", "-")) or {}
    status = live.get("status", "stopped")
    stats = _stats(live.get("pid"))
    return {
        "name": name,
        "state": status,
        "pid": live.get("pid"),
        "log_filename": f"{name}.log",
        "declaration": declaration,
        **stats,
    }


def _declaration_payload(pd) -> dict:
    import shlex

    return {
        "cmd": shlex.join(pd.argv),
        "working_dir": str(pd.working_dir) if pd.working_dir else None,
        "restart_on_failure": pd.restart_on_failure,
        "stop_timeout": pd.stop_timeout,
        "has_hooks": bool(pd.pre_run or pd.post_run),
    }


@processes_bp.get("")
def list_processes():
    bench = _bench()
    manager = _manager()
    builder = ProcessDefinitionBuilder(bench, manager.python, bench.config.watch_admin_js)

    try:
        states = manager.live_states()
    except Exception:
        return error_response("processes_unavailable", "Could not read process status.", 500)

    try:
        all_defs = builder.prod_process_definitions()
        app_names = {pd.name for pd in builder.app_process_definitions()}
        bench_defs = [pd for pd in all_defs if pd.name not in app_names]
    except AppRequirementsError as exc:
        app_name, _, message = str(exc).partition(": ")
        return jsonify({"groups": [], "blocked": {"app": app_name.strip("'"), "message": message}})
    except Exception:
        return error_response("processes_unavailable", "Could not read process declarations.", 500)

    groups = [
        {
            "source": "bench",
            "processes": [_row(pd.name, states) for pd in bench_defs],
        }
    ]
    for app in bench.apps():
        app_defs = app.requirements.process_definitions()
        if not app_defs:
            continue
        groups.append(
            {
                "source": app.config.name,
                "processes": [_row(pd.name, states, _declaration_payload(pd)) for pd in app_defs],
            }
        )

    return jsonify({"groups": groups, "blocked": None})


@processes_bp.post("/<name>/actions/restart")
def restart_process(name: str):
    manager = _manager()
    if not hasattr(manager, "apply_process_action"):
        return error_response(
            "process_control_unavailable",
            "Restarting one process needs systemd or supervisor; the dev runner is status-only.",
            409,
        )
    try:
        manager.apply_process_action("restart", name)
    except Exception as exc:
        return error_response("process_action_failed", str(exc), 500)
    return jsonify({"ok": True})


@processes_bp.post("/actions/restart")
def restart_workload():
    manager = _manager()
    try:
        manager.restart()
    except Exception as exc:
        return error_response("process_action_failed", str(exc), 500)
    return jsonify({"ok": True})
