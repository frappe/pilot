from __future__ import annotations

import copy
import secrets
from dataclasses import asdict
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file

from admin.backend.tasks.callbacks import new_site_failure_callback, ssl_setup_failure_callback
from ..validators import validate_cron_expression, validate_site_name
from admin.backend.tasks.manager.task_runner import TaskRunner

from ..readers.app_reader import AppReader
from ..readers.site_reader import SiteReader

sites_bp = Blueprint("sites", __name__)

# Confidential / system-managed site_config keys. These are never sent to the
# admin UI and cannot be edited through it — they are preserved as-is on disk.
PROTECTED_CONFIG_KEYS = frozenset({"db_name", "db_password", "db_socket", "db_type", "db_user", "installed_apps", "ssl"})


@sites_bp.route("/")
def index():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        sites = SiteReader(bench_root).read_all()
    except Exception as error:
        return jsonify({"error": str(error)}), 500

    payload = []
    for s in sites:
        d = asdict(s)
        d["site_config"] = _public_config(s.site_config)
        payload.append(d)
    return jsonify(payload)


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

    from bench_cli.config.bench_config import BenchConfig

    try:
        bench_config = BenchConfig.from_file(bench_root / "bench.toml")
        http_port = bench_config.http_port
        nginx_enabled = bench_config.production.enabled
        admin_tls = bench_config.admin.tls
    except Exception:
        http_port = 8000
        nginx_enabled = False
        admin_tls = False

    site_dict = asdict(site)
    site_dict["site_config"] = _public_config(site.site_config)
    site_dict["ssl"] = bool(site.site_config.get("ssl"))
    return jsonify({"site": site_dict, "installable_apps": installable, "http_port": http_port, "nginx_enabled": nginx_enabled, "admin_tls": admin_tls})


@sites_bp.route("/<name>/apps")
def site_apps(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        site = SiteReader(bench_root).read_one(name)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 404

    reader = AppReader(bench_root)
    result = []
    for app_name in site.installed_apps:
        try:
            info = reader.read_one(app_name)
            result.append(
                {
                    "name": app_name,
                    "branch": info.branch,
                    "commit": info.current_commit,
                    "version": info.installed_version,
                    "repo": info.repo,
                    "has_local_changes": info.has_local_changes,
                }
            )
        except Exception:
            result.append(
                {
                    "name": app_name,
                    "branch": "",
                    "commit": "",
                    "version": "",
                    "repo": "",
                }
            )

    return jsonify({"apps": result})


@sites_bp.route("/create", methods=["POST"])
def create():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    admin_password = (data.get("admin_password") or "admin").strip() or "admin"
    err = validate_site_name(name) or _new_site_name_error(bench_root, name)
    if err:
        return jsonify({"ok": False, "error": err})

    try:
        task_id = TaskRunner(bench_root).run(
            "new-site",
            {"name": name, "admin_password": admin_password},
            callbacks={"on_failure": new_site_failure_callback},
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not start new-site: {e}"})

    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/create-from-upload", methods=["POST"])
def create_from_upload():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    name = (request.form.get("name") or "").strip()
    admin_password = (request.form.get("admin_password") or "admin").strip() or "admin"
    err = validate_site_name(name) or _new_site_name_error(bench_root, name)
    if err:
        return jsonify({"ok": False, "error": err})

    db_upload = request.files.get("db_file")
    if not db_upload:
        return jsonify({"ok": False, "error": "Database backup file is required."})

    upload_dir = bench_root / "tmp" / "uploads" / secrets.token_hex(8)
    upload_dir.mkdir(parents=True)

    db_path = upload_dir / db_upload.filename
    db_upload.save(str(db_path))

    args = {"name": name, "admin_password": admin_password, "db_file": str(db_path)}

    pub_upload = request.files.get("public_files")
    if pub_upload:
        pub_path = upload_dir / pub_upload.filename
        pub_upload.save(str(pub_path))
        args["public_files"] = str(pub_path)

    priv_upload = request.files.get("private_files")
    if priv_upload:
        priv_path = upload_dir / priv_upload.filename
        priv_upload.save(str(priv_path))
        args["private_files"] = str(priv_path)

    try:
        task_id = TaskRunner(bench_root).run("new-site-from-backup", args)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/<name>/drop", methods=["POST"])
def drop_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        task_id = TaskRunner(bench_root).run("drop-site", {"site": name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/<name>/reinstall", methods=["POST"])
def reinstall_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not (bench_root / "sites" / name / "site_config.json").exists():
        return jsonify({"ok": False, "error": "Site not found."}), 404
    data = request.get_json(silent=True) or {}
    admin_password = (data.get("admin_password") or "admin").strip() or "admin"
    try:
        task_id = TaskRunner(bench_root).run("reinstall-site", {"site": name, "admin_password": admin_password})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/<name>/force-drop", methods=["POST"])
def force_drop_site(name: str):
    import shutil

    bench_root = Path(current_app.config["BENCH_ROOT"])
    site_path = bench_root / "sites" / name
    if not (site_path / "site_config.json").exists():
        return jsonify({"ok": False, "error": "Site not found."}), 404
    try:
        shutil.rmtree(site_path)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})


@sites_bp.route("/<name>/backup", methods=["POST"])
def backup_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        task_id = TaskRunner(bench_root).run("backup-site", {"site": name, "with_files": True})
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


@sites_bp.route("/<name>/get-and-install-app", methods=["POST"])
def get_and_install_app(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    app = (data.get("app") or "").strip()
    repo = (data.get("repo") or "").strip()
    branch = (data.get("branch") or "").strip()
    if not repo:
        return jsonify({"ok": False, "error": "Repo URL is required."})
    if not app:
        from bench_cli.core.git_providers import GitProviderError, resolve_app_name_from_repo

        try:
            app = resolve_app_name_from_repo(bench_root, repo, branch)
        except GitProviderError as e:
            return jsonify({"ok": False, "error": f"Could not determine app name: {e}"})
        except Exception as e:
            return jsonify({"ok": False, "error": f"Could not read pyproject.toml: {e}"})
    try:
        task_args = {"site": name, "app": app, "repo": repo}
        if branch:
            task_args["branch"] = branch
        task_id = TaskRunner(bench_root).run("get-and-install-app", task_args)
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


@sites_bp.route("/<name>/force-uninstall-app", methods=["POST"])
def force_uninstall_app(name: str):
    import os
    import subprocess as _sp

    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}

    from ..validators import validate_app_name

    app = (data.get("app") or "").strip()
    err = validate_app_name(app)
    if err:
        return jsonify({"ok": False, "error": err})

    if not (bench_root / "sites" / name / "site_config.json").exists():
        return jsonify({"ok": False, "error": "Site not found."}), 404

    python = str(bench_root / "env" / "bin" / "python")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    try:
        result = _sp.run(
            [
                python,
                "-m",
                "frappe.utils.bench_helper",
                "frappe",
                "--site",
                name,
                "execute",
                "frappe.installer.remove_from_installed_apps",
                "--args",
                f'["{app}"]',
            ],
            cwd=str(bench_root / "sites"),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if result.returncode != 0:
            return jsonify({"ok": False, "error": result.stderr.strip() or "Force remove failed."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

    return jsonify({"ok": True})


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
        bench_config = BenchConfig.from_file(bench_root / "bench.toml")
        http_port = bench_config.http_port
        nginx_enabled = bench_config.production.enabled
    except Exception:
        http_port = 8000
        nginx_enabled = False

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

    # Behind nginx the site is served by domain on 80/443; only dev talks to the gunicorn port directly.
    if nginx_enabled:
        import json

        try:
            ssl = bool(json.loads((bench_root / "sites" / name / "site_config.json").read_text()).get("ssl"))
        except Exception:
            ssl = False
        url = f"{'https' if ssl else 'http'}://{name}/desk?sid={sid}"
    else:
        url = f"http://{name}:{http_port}/desk?sid={sid}"

    return jsonify({"ok": True, "url": url})


@sites_bp.route("/<name>/enable-ssl", methods=["POST"])
def enable_ssl(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    config_path = bench_root / "sites" / name / "site_config.json"
    if not config_path.exists():
        return jsonify({"ok": False, "error": "Site not found."}), 404

    from bench_cli.config.bench_config import BenchConfig
    from bench_cli.config.toml_writer import bench_config_to_toml

    from ..validators import validate_email

    try:
        config = BenchConfig.from_file(bench_root / "bench.toml")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    # Let's Encrypt needs an ACME account email; persist one if the UI supplied it.
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    if email:
        if err := validate_email(email):
            return jsonify({"ok": False, "error": err, "needs_email": True})
        config.letsencrypt.email = email
        try:
            (bench_root / "bench.toml").write_text(bench_config_to_toml(config))
        except Exception as e:
            return jsonify({"ok": False, "error": f"Failed to save email: {e}"}), 500

    # No email anywhere — ask the UI to collect one instead of starting a doomed task.
    if not config.letsencrypt.email:
        return jsonify(
            {
                "ok": False,
                "needs_email": True,
                "error": "A Let's Encrypt account email is required to issue certificates.",
            }
        )

    import json

    current = json.loads(config_path.read_text())
    current["ssl"] = True
    config_path.write_text(json.dumps(current, indent=1))

    try:
        task_id = TaskRunner(bench_root).run(
            "setup-letsencrypt",
            {"site": name},
            callbacks={"on_failure": ssl_setup_failure_callback},
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


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

    # The editable keys are whatever the UI sent, minus any protected key it may
    # have included; protected keys are always preserved from the on-disk config.
    editable = {k: v for k, v in data.items() if k not in PROTECTED_CONFIG_KEYS}
    preserved = {k: v for k, v in current.items() if k in PROTECTED_CONFIG_KEYS}
    merged = {**editable, **preserved}

    config_path.write_text(json.dumps(merged, indent=1))
    return jsonify({"ok": True})


@sites_bp.route("/<name>/backups")
def list_backups(name: str):
    from ..readers.backup_reader import BackupReader

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        sets = BackupReader(bench_root, name).read_all()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(
        [
            {
                "timestamp": s.timestamp,
                "created_at": s.created_at.isoformat(),
                "files": [
                    {
                        "filename": f.filename,
                        "path": f.path,
                        "size_bytes": f.size_bytes,
                        "kind": f.kind,
                    }
                    for f in s.files
                ],
            }
            for s in sets
        ]
    )


@sites_bp.route("/<name>/backups/download")
def download_backup(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    filename = request.args.get("filename", "")
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        return jsonify({"error": "Invalid filename."}), 400

    backups_dir = (bench_root / "sites" / name / "private" / "backups").resolve()
    target = (backups_dir / filename).resolve()
    if backups_dir not in target.parents or not target.is_file():
        return jsonify({"error": "Backup file not found."}), 404

    return send_file(target, as_attachment=True, download_name=filename)


@sites_bp.route("/<name>/backup-schedule", methods=["GET"])
def get_backup_schedule(name: str):
    from ..cron_manager import CronManager

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        schedule = CronManager(bench_root).get_schedule(name)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"schedule": schedule})


@sites_bp.route("/<name>/backup-schedule", methods=["POST"])
def set_backup_schedule(name: str):
    from ..cron_manager import CronManager

    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    schedule = (data.get("schedule") or "").strip()
    err = validate_cron_expression(schedule)
    if err:
        return jsonify({"ok": False, "error": err})
    try:
        CronManager(bench_root).set_schedule(name, schedule)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True})


@sites_bp.route("/<name>/backup-schedule", methods=["DELETE"])
def delete_backup_schedule(name: str):
    from ..cron_manager import CronManager

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        CronManager(bench_root).remove_schedule(name)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True})


def _new_site_name_error(bench_root: Path, name: str) -> str | None:
    """Validate a new-site name before any task starts, so the error lands in the UI
    instead of failing mid-run. Mirrors NewSiteCommand._validate."""
    from bench_cli.config.bench_config import BenchConfig
    from bench_cli.utils import host_owner, normalize_host

    if (bench_root / "sites" / name / "site_config.json").exists():
        return f"Site '{name}' already exists."

    owner = host_owner(bench_root, name)
    if owner:
        return f"'{name}' is already used by bench '{owner}' (as a site or its admin domain). All benches share one nginx, so hostnames must be unique."

    try:
        admin_domain = BenchConfig.from_file(bench_root / "bench.toml").admin.domain
    except Exception:
        admin_domain = ""
    if admin_domain and normalize_host(name) == normalize_host(admin_domain):
        return f"Site '{name}' clashes with this bench's admin domain. An admin domain must not match a site domain."
    return None


def _public_config(config: dict) -> dict:
    """Drop confidential / system-managed keys before exposing site_config."""
    return {k: copy.deepcopy(v) for k, v in config.items() if k not in PROTECTED_CONFIG_KEYS}
