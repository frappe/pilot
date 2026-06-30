from __future__ import annotations

import subprocess
from dataclasses import asdict
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from ..readers.app_reader import AppReader
from ..validators import validate_app_name, validate_repo_url
from admin.backend.tasks.manager.task_runner import TaskRunner

apps_bp = Blueprint("apps", __name__)


@apps_bp.route("/")
def index():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        apps = AppReader(bench_root).read_all()
    except Exception as error:
        return jsonify({"error": str(error)}), 500
    return jsonify([asdict(a) for a in apps])


@apps_bp.route("/marketplace")
def marketplace():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        from pilot.core.bench import Bench
        from pilot.core.marketplace import Marketplace
        from pilot.config.toml_store import BenchTomlStore

        bench = Bench(BenchTomlStore.for_bench(bench_root).read(), bench_root)
        apps = Marketplace(bench).read_all_apps()
        return jsonify([a.to_dict() for a in apps])
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@apps_bp.route("/add", methods=["POST"])
def add():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    err = validate_app_name(name)
    if err:
        return jsonify({"ok": False, "error": err})

    if (bench_root / "apps" / name / ".git").exists():
        return jsonify({"ok": False, "error": f"'{name}' is already installed."})

    try:
        task_id = TaskRunner(bench_root).run(
            "get-app", {"name": name, "marketplace_app": name}
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not start get-app: {e}"})

    return jsonify({"ok": True, "task_id": task_id})


@apps_bp.route("/add-and-install", methods=["POST"])
def add_and_install():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    sites = data.get("sites") or []

    err = validate_app_name(name)
    if err:
        return jsonify({"ok": False, "error": err})

    if not isinstance(sites, list):
        return jsonify({"ok": False, "error": "sites must be a list."})

    if (bench_root / "apps" / name / ".git").exists():
        return jsonify({"ok": False, "error": f"'{name}' is already installed."})

    try:
        task_args = {"name": name, "marketplace_app": name, "sites": sites}
        task_id = TaskRunner(bench_root).run("add-and-install-app", task_args)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not start add-and-install: {e}"})

    return jsonify({"ok": True, "task_id": task_id})


@apps_bp.route("/<name>/remove", methods=["POST"])
def remove(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])

    if not (bench_root / "apps" / name).exists():
        return jsonify({"ok": False, "error": f"App '{name}' not found in bench."})

    try:
        task_id = TaskRunner(bench_root).run("remove-app", {"name": name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

    return jsonify({"ok": True, "task_id": task_id})


@apps_bp.route("/<name>/set-upstream", methods=["POST"])
def set_upstream(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    repo = (data.get("repo") or "").strip()

    err = validate_repo_url(repo)
    if err:
        return jsonify({"ok": False, "error": err})

    app_path = bench_root / "apps" / name
    if not (app_path / ".git").exists():
        return jsonify({"ok": False, "error": f"App '{name}' not found"})

    result = subprocess.run(
        ["git", "-C", str(app_path), "remote", "set-url", "origin", repo],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return jsonify({"ok": False, "error": result.stderr.strip() or "Failed to update remote URL"})

    return jsonify({"ok": True})


@apps_bp.route("/fetch", methods=["POST"])
def fetch_updates():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    task_id = TaskRunner(bench_root).run("fetch-all-app-updates", {})
    return jsonify({"task_id": task_id})


