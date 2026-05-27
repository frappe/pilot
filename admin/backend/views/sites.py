from __future__ import annotations

import copy
from dataclasses import asdict
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from ..readers.app_reader import AppReader
from ..readers.site_reader import SiteReader
from admin.backend.tasks.manager.task_runner import TaskRunner

sites_bp = Blueprint("sites", __name__)


@sites_bp.route("/")
def index():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        sites = SiteReader(bench_root).read_all()
    except Exception as error:
        return jsonify({"error": str(error)}), 500
    return jsonify([asdict(s) for s in sites])


@sites_bp.route("/<name>")
def detail(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        site = SiteReader(bench_root).read_one(name)
    except Exception as error:
        return jsonify({"error": str(error)}), 500

    # Installable = apps that are cloned but not yet installed on this site
    try:
        all_apps = [a.name for a in AppReader(bench_root).read_all()]
        installable = [a for a in all_apps if a not in site.installed_apps]
    except Exception:
        installable = []

    site_dict = asdict(site)
    site_dict["site_config"] = _mask_password(site.site_config)
    return jsonify({"site": site_dict, "installable_apps": installable})


@sites_bp.route("/create", methods=["POST"])
def create():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    admin_password = (data.get("admin_password") or "admin").strip() or "admin"
    if not name:
        return jsonify({"ok": False, "error": "Site name is required."})

    # Check site doesn't already exist
    if (bench_root / "sites" / name / "site_config.json").exists():
        return jsonify({"ok": False, "error": f"Site '{name}' already exists."})

    try:
        task_id = TaskRunner(bench_root).run(
            "new-site", {"name": name, "admin_password": admin_password}
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not start new-site: {e}"})

    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/<name>/drop", methods=["POST"])
def drop_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        task_id = TaskRunner(bench_root).run("drop-site", {"site": name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/<name>/backup", methods=["POST"])
def backup_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        task_id = TaskRunner(bench_root).run("backup-site", {"site": name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/<name>/install-app", methods=["POST"])
def install_app(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    app = (data.get("app") or "").strip()
    if not app:
        return jsonify({"ok": False, "error": "App name is required."})
    try:
        task_id = TaskRunner(bench_root).run("install-app", {"site": name, "app": app})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/<name>/uninstall-app", methods=["POST"])
def uninstall_app(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    app = (data.get("app") or "").strip()
    if not app:
        return jsonify({"ok": False, "error": "App name is required."})
    try:
        task_id = TaskRunner(bench_root).run("uninstall-app", {"site": name, "app": app})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/<name>/login", methods=["POST"])
def login_to_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not (bench_root / "sites" / name / "site_config.json").exists():
        return jsonify({"ok": False, "error": "Site not found."}), 404

    data = request.get_json(silent=True) or {}
    password = (data.get("password") or "").strip()
    if not password:
        return jsonify({"ok": False, "error": "Password is required."})

    import http.client
    import urllib.parse
    from bench_cli.config.bench_config import BenchConfig

    try:
        http_port = BenchConfig.from_file(bench_root / "bench.toml").http_port
    except Exception:
        http_port = 8000

    try:
        conn = http.client.HTTPConnection("localhost", http_port, timeout=10)
        conn.request(
            "POST",
            "/api/method/login",
            body=urllib.parse.urlencode({"usr": "Administrator", "pwd": password}),
            headers={
                "Host": name,
                "X-Frappe-Site-Name": name,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        resp = conn.getresponse()
    except OSError as e:
        return jsonify({"ok": False, "error": f"Could not reach site web server: {e}"})

    if resp.status == 401:
        return jsonify({"ok": False, "error": "Incorrect password."})
    if resp.status != 200:
        return jsonify({"ok": False, "error": f"Site returned HTTP {resp.status}."})

    sid = None
    for header, value in resp.getheaders():
        if header.lower() == "set-cookie" and value.startswith("sid="):
            sid = value.split("=", 1)[1].split(";")[0]
            break

    if not sid or sid == "Guest":
        return jsonify({"ok": False, "error": "Login failed — wrong password?"})

    return jsonify({"ok": True, "url": f"http://{name}:{http_port}/desk?sid={sid}"})


@sites_bp.route("/<name>/config", methods=["PATCH"])
def update_config(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    config_path = bench_root / "sites" / name / "site_config.json"
    if not config_path.exists():
        return jsonify({"ok": False, "error": "site_config.json not found."}), 404

    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Invalid JSON body."}), 400

    import json
    current = json.loads(config_path.read_text())

    # Preserve the real db_password if the masked sentinel came back
    _MASK = "••••••••"
    if data.get("db_password") == _MASK:
        if "db_password" in current:
            data["db_password"] = current["db_password"]
        else:
            del data["db_password"]

    config_path.write_text(json.dumps(data, indent=1))
    return jsonify({"ok": True})


def _mask_password(config: dict) -> dict:
    masked = copy.deepcopy(config)
    if "db_password" in masked:
        masked["db_password"] = "••••••••"
    return masked
