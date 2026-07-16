from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, current_app, jsonify

from admin.backend.api.responses import error_response
from pilot.tasks.manager.task_reader import TaskReader

from ...readers.apps import AppReader
from ...readers.benches import BenchReader
from ...readers.processes import ProcessReader
from ...readers.sites import SiteReader

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
    except Exception:
        return error_response("dashboard_unavailable", "Could not load dashboard.", 500)

    return jsonify(
        {
            "summary": asdict(summary),
            "apps": [asdict(a) for a in apps],
            "sites": [asdict(s) for s in sites],
            "processes": [_proc_dict(p) for p in processes],
            "recent_tasks": [_task_dict(t) for t in recent_tasks],
            "running_count": sum(1 for p in processes if p.status == "running"),
            "cloned_count": sum(1 for a in apps if a.is_cloned),
            "online_count": sum(1 for s in sites if s.exists),
        }
    )


def _proc_dict(p):
    return {
        "name": p.name,
        "status": p.status,
        "pid": p.pid,
        "uptime": p.uptime,
        "log_filename": p.log_file.name,
    }


def _task_dict(t):
    return t.as_dict()
