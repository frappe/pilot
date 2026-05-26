from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, current_app, jsonify

from ..readers.app_reader import AppReader
from ..readers.bench_reader import BenchReader
from ..readers.process_reader import ProcessReader
from ..readers.site_reader import SiteReader
from admin.backend.tasks.manager.task_reader import TaskReader

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
def index():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        summary = BenchReader(bench_root).summary()
        apps = AppReader(bench_root).read_all()
        sites = SiteReader(bench_root).read_all()
        processes = ProcessReader(bench_root).read_all()
        recent_tasks = TaskReader(bench_root).list_tasks(limit=5)
    except Exception as error:
        return jsonify({"error": str(error)}), 500

    return jsonify({
        "summary": asdict(summary),
        "apps": [asdict(a) for a in apps],
        "sites": [asdict(s) for s in sites],
        "processes": [_proc_dict(p) for p in processes],
        "recent_tasks": [_task_dict(t) for t in recent_tasks],
        "running_count": sum(1 for p in processes if p.status == "running"),
        "cloned_count": sum(1 for a in apps if a.is_cloned),
        "online_count": sum(1 for s in sites if s.exists),
    })


def _proc_dict(p):
    return {
        "name": p.name,
        "status": p.status,
        "pid": p.pid,
        "uptime": p.uptime,
        "log_filename": p.log_file.name,
    }


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
