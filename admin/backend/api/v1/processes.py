from __future__ import annotations

import subprocess
from pathlib import Path

from flask import Blueprint, current_app, jsonify

from pilot.config.toml_store import BenchTomlStore

from ...api.responses import error_response
from ...readers.processes import ProcessReader

processes_bp = Blueprint("processes", __name__)


def _bench_name(bench_root: Path) -> str:
    try:
        return BenchTomlStore.for_bench(bench_root).read_raw().get("bench", {}).get("name", "bench")
    except Exception:
        return "bench"


def _supervisor_conf(bench_root: Path) -> Path | None:
    p = bench_root / "config" / "supervisor" / "supervisord.conf"
    return p if p.exists() else None


def _supervisorctl(conf: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["supervisorctl", "-c", str(conf), *args],
        capture_output=True, text=True, timeout=30,
    )


def _non_admin_programs(conf: Path, bench_name: str) -> list[str]:
    """Return all supervisor program names in the bench group except admin."""
    result = _supervisorctl(conf, "status", f"{bench_name}:*")
    programs = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        full_name = line.split()[0]  # e.g. "frappe:frappe-web"
        if not full_name.endswith("-admin"):
            programs.append(full_name)
    return programs


def _process_list_response():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        processes = ProcessReader(bench_root).read_all()
    except Exception:
        return error_response("processes_unavailable", "Could not read process status.", 500)

    conf = _supervisor_conf(bench_root)
    return jsonify({
        "processes": [
            {
                "name": p.name,
                "status": p.status,
                "pid": p.pid,
                "uptime": p.uptime,
                "cpu_percent": p.cpu_percent,
                "rss_mb": p.rss_mb,
                "pss_mb": p.pss_mb,
                "log_filename": p.log_file.name,
            }
            for p in processes
        ],
        "production": conf is not None,
    })


@processes_bp.get("/processes")
def index():
    return _process_list_response()


@processes_bp.post("/actions/restart")
def restart():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    conf = _supervisor_conf(bench_root)
    if conf is None:
        return error_response(
            "process_control_unavailable",
            "Restart is only supported in production mode.",
            409,
        )

    bench = _bench_name(bench_root)
    programs = _non_admin_programs(conf, bench)
    if not programs:
        return error_response("no_running_processes", "No running processes were found.", 409)

    result = _supervisorctl(conf, "restart", *programs)
    if result.returncode != 0:
        return error_response("process_restart_failed", "Could not restart processes.", 500)
    return _process_list_response()


@processes_bp.post("/actions/stop")
def stop():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    conf = _supervisor_conf(bench_root)
    if conf is None:
        return error_response(
            "process_control_unavailable",
            "Stop is only supported in production mode.",
            409,
        )

    bench = _bench_name(bench_root)
    programs = _non_admin_programs(conf, bench)
    if not programs:
        return error_response("no_running_processes", "No processes are available to stop.", 409)

    result = _supervisorctl(conf, "stop", *programs)
    if result.returncode != 0:
        return error_response("process_stop_failed", "Could not stop processes.", 500)
    return _process_list_response()


@processes_bp.post("/actions/start")
def start():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    conf = _supervisor_conf(bench_root)
    if conf is None:
        return error_response(
            "process_control_unavailable",
            "Start is only supported in production mode.",
            409,
        )

    bench = _bench_name(bench_root)
    result = _supervisorctl(conf, "start", f"{bench}:*")
    if result.returncode != 0:
        return error_response("process_start_failed", "Could not start processes.", 500)
    return _process_list_response()
