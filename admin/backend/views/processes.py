from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from flask import Blueprint, current_app, jsonify

from ..readers.process_reader import ProcessReader

processes_bp = Blueprint("processes", __name__)


def _bench_name(bench_root: Path) -> str:
    try:
        with open(bench_root / "bench.toml", "rb") as f:
            return tomllib.load(f).get("bench", {}).get("name", "bench")
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


@processes_bp.route("/")
def index():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        processes = ProcessReader(bench_root).read_all()
    except Exception as error:
        return jsonify({"error": str(error)}), 500

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


@processes_bp.route("/restart", methods=["POST"])
def restart():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    conf = _supervisor_conf(bench_root)
    if conf is None:
        return jsonify({"ok": False, "error": "Restart is only supported in production mode."})

    bench = _bench_name(bench_root)
    programs = _non_admin_programs(conf, bench)
    if not programs:
        return jsonify({"ok": False, "error": "No running processes found."})

    result = _supervisorctl(conf, "restart", *programs)
    if result.returncode != 0:
        return jsonify({"ok": False, "error": result.stderr or result.stdout})
    return jsonify({"ok": True})


@processes_bp.route("/stop", methods=["POST"])
def stop():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    conf = _supervisor_conf(bench_root)
    if conf is None:
        return jsonify({"ok": False, "error": "Stop is only supported in production mode."})

    bench = _bench_name(bench_root)
    programs = _non_admin_programs(conf, bench)
    if not programs:
        return jsonify({"ok": False, "error": "No processes to stop."})

    result = _supervisorctl(conf, "stop", *programs)
    if result.returncode != 0:
        return jsonify({"ok": False, "error": result.stderr or result.stdout})
    return jsonify({"ok": True})


@processes_bp.route("/start", methods=["POST"])
def start():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    conf = _supervisor_conf(bench_root)
    if conf is None:
        return jsonify({"ok": False, "error": "Start is only supported in production mode."})

    bench = _bench_name(bench_root)
    result = _supervisorctl(conf, "start", f"{bench}:*")
    if result.returncode != 0:
        return jsonify({"ok": False, "error": result.stderr or result.stdout})
    return jsonify({"ok": True})
