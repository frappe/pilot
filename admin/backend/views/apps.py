from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from ..readers.app_reader import AppReader
from admin.backend.tasks.manager.task_runner import TaskRunner

apps_bp = Blueprint("apps", __name__)

_REGISTRY_PATH = Path(__file__).parent.parent.parent.parent / "registry" / "apps.json"


@apps_bp.route("/")
def index():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        apps = AppReader(bench_root).read_all()
    except Exception as error:
        return jsonify({"error": str(error)}), 500
    return jsonify([asdict(a) for a in apps])


@apps_bp.route("/registry")
def registry():
    try:
        return jsonify(json.loads(_REGISTRY_PATH.read_text()))
    except Exception:
        return jsonify([])


@apps_bp.route("/add", methods=["POST"])
def add():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    repo = (data.get("repo") or "").strip()
    branch = (data.get("branch") or "").strip()

    if not name:
        return jsonify({"ok": False, "error": "App name is required."})
    if not repo:
        return jsonify({"ok": False, "error": "Repository URL is required."})

    # Check app isn't already cloned
    if (bench_root / "apps" / name / ".git").exists():
        return jsonify({"ok": False, "error": f"'{name}' is already installed."})

    try:
        task_id = TaskRunner(bench_root).run(
            "get-app", {"name": name, "repo": repo, "branch": branch}
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not start get-app: {e}"})

    return jsonify({"ok": True, "task_id": task_id})


@apps_bp.route("/<name>/switch-branch", methods=["POST"])
def switch_branch(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}

    branch = (data.get("branch") or "").strip()
    if not branch:
        return jsonify({"ok": False, "error": "branch is required."})

    # Verify app is cloned
    if not (bench_root / "apps" / name / ".git").exists():
        return jsonify({"ok": False, "error": f"App '{name}' is not installed."})

    try:
        task_id = TaskRunner(bench_root).run(
            "switch-branch", {"name": name, "branch": branch}
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

    return jsonify({"ok": True, "task_id": task_id})
