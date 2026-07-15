from __future__ import annotations

import copy
import secrets
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file

from pilot.secure_files import write_private_text

from admin.backend.auth import require_scope
from admin.backend.uploads import (
    UploadError,
    create_upload_directory,
    save_archive_upload,
    save_database_upload,
)
from ..validators import validate_cron_expression, validate_site_name
from admin.backend.tasks.manager.task_runner import TaskRunner

from ..readers.app_reader import AppReader
from ..readers.site_reader import SiteReader


def site_name(kwargs: dict) -> str:
    return kwargs["name"]


sites_bp = Blueprint("sites", __name__)

# Confidential / system-managed site_config keys. These are never sent to the
# admin UI and cannot be edited through it — they are preserved as-is on disk.
PROTECTED_CONFIG_KEYS = frozenset({"db_name", "db_password", "db_socket", "db_type", "db_user", "installed_apps", "ssl", "domains", "host_name", "pilot_endpoint", "pilot_auth_token", "backup_retention"})
_SENSITIVE_CONFIG_KEY_PARTS = (
    "_key",
    "access_key",
    "api_key",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)


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
@require_scope(site_name)
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

    from pilot.config.toml_store import BenchTomlStore

    try:
        bench_config = BenchTomlStore.for_bench(bench_root).read()
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
@require_scope(site_name)
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
                    "title": info.title,
                    "description": info.description,
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
                    "title": app_name,
                    "description": "",
                    "branch": "",
                    "commit": "",
                    "version": "",
                    "repo": "",
                }
            )

    return jsonify({"apps": result})


@sites_bp.route("/wildcard-domains", methods=["GET"])
def wildcard_domains():
    """Wildcard domain suffixes (no leading '*') new site names may be built from."""
    from pilot.core.domain_controller import DomainRouteProvider
    from pilot.utils import wildcard_suffix

    try:
        patterns = DomainRouteProvider.wildcard_domains()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"domains": [wildcard_suffix(p) for p in patterns]})


@sites_bp.route("/create", methods=["POST"])
def create():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    admin_password = secrets.token_urlsafe(16)
    db_type = (data.get("db_type") or "").strip()
    apps = [app.strip() for app in data.get("apps") or [] if isinstance(app, str) and app.strip()]
    if db_type and db_type not in ("mariadb", "postgres", "sqlite"):
        return jsonify({"ok": False, "error": f"Invalid db_type '{db_type}'."})
    err = validate_site_name(name) or _new_site_name_error(bench_root, name)
    if err:
        return jsonify({"ok": False, "error": err})

    task_args: dict = {"name": name, "admin_password": admin_password}
    if db_type:
        task_args["db_type"] = db_type
    if apps:
        task_args["apps"] = apps
    try:
        task_id = TaskRunner(bench_root).run(
            "new-site",
            task_args,
            callbacks={
                "on_failure": {
                    "operation": "remove-failed-site",
                    "args": {"site": name},
                }
            },
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not start new-site: {e}"})

    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/create-from-upload", methods=["POST"])
def create_from_upload():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    name = (request.form.get("name") or "").strip()
    admin_password = request.form.get("admin_password")
    if not isinstance(admin_password, str) or not admin_password.strip():
        admin_password = secrets.token_urlsafe(16)
    err = validate_site_name(name) or _new_site_name_error(bench_root, name)
    if err:
        return jsonify({"ok": False, "error": err})

    db_upload = request.files.get("db_file")
    if not db_upload:
        return jsonify({"ok": False, "error": "Database backup file is required."})

    upload_dir = None
    try:
        upload_dir = create_upload_directory(bench_root)
        db_path = save_database_upload(db_upload, upload_dir)
        args = {"name": name, "admin_password": admin_password, "db_file": str(db_path)}

        pub_upload = request.files.get("public_files")
        if pub_upload:
            args["public_files"] = str(save_archive_upload(pub_upload, upload_dir, "public files"))

        priv_upload = request.files.get("private_files")
        if priv_upload:
            args["private_files"] = str(
                save_archive_upload(priv_upload, upload_dir, "private files")
            )
    except (OSError, UploadError) as exc:
        if upload_dir:
            shutil.rmtree(upload_dir, ignore_errors=True)
        return jsonify({"ok": False, "error": str(exc)}), 400

    try:
        task_id = TaskRunner(bench_root).run("new-site-from-backup", args)
    except Exception as e:
        shutil.rmtree(upload_dir, ignore_errors=True)
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/<name>/drop", methods=["POST"])
@require_scope(site_name)
def drop_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        task_id = TaskRunner(bench_root).run("drop-site", {"site": name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/<name>/reinstall", methods=["POST"])
@require_scope(site_name)
def reinstall_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not (bench_root / "sites" / name / "site_config.json").exists():
        return jsonify({"ok": False, "error": "Site not found."}), 404
    data = request.get_json(silent=True) or {}
    admin_password = data.get("admin_password")
    if not isinstance(admin_password, str) or not admin_password.strip():
        admin_password = secrets.token_urlsafe(16)
    try:
        task_id = TaskRunner(bench_root).run(
            "reinstall-site", {"site": name, "admin_password": admin_password}
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/<name>/force-drop", methods=["POST"])
@require_scope(site_name)
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
@require_scope(site_name)
def backup_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        task_id = TaskRunner(bench_root).run("backup-site", {"site": name, "with_files": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/<name>/clear-cache", methods=["POST"])
@require_scope(site_name)
def clear_cache(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        task_id = TaskRunner(bench_root).run("clear-cache", {"site": name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/<name>/migrate", methods=["POST"])
@require_scope(site_name)
def migrate_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        task_id = TaskRunner(bench_root).run("migrate", {"site": name})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/<name>/install-app", methods=["POST"])
@require_scope(site_name)
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
@require_scope(site_name)
def get_and_install_app(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    app = (data.get("app") or "").strip()
    repo = (data.get("repo") or "").strip()
    target = (data.get("target") or data.get("branch") or "").strip()

    if app:
        task_args = {"site": name, "app": app, "marketplace_app": app}
    else:
        if not repo:
            return jsonify({"ok": False, "error": "Repo URL is required."})
        from pilot.core.git_providers import GitProviderError, resolve_app_name_from_repo

        try:
            app = resolve_app_name_from_repo(bench_root, repo, target)["name"]
        except GitProviderError as e:
            return jsonify({"ok": False, "error": f"Could not determine app name: {e}"})
        except Exception as e:
            return jsonify({"ok": False, "error": f"Could not read pyproject.toml: {e}"})
        task_args = {"site": name, "app": app, "repo": repo}
        if target:
            task_args["branch"] = target

    try:
        task_id = TaskRunner(bench_root).run("get-and-install-app", task_args)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/<name>/uninstall-app", methods=["POST"])
@require_scope(site_name)
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
@require_scope(site_name)
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


def _get_site_sid(bench_root: Path, site: str, user: str = "Administrator") -> tuple[str | None, str]:
    import re

    # bench binary lives at project root; bench_root is <project>/benches/<name>
    bench_bin = bench_root.parent.parent / "bench"
    bench_name = bench_root.name
    benches_dir = bench_root.parent

    result = subprocess.run(
        [str(bench_bin), "-b", bench_name, "--site", site, "browse", "--user", user],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(benches_dir),
    )
    output = (result.stdout or "") + (result.stderr or "")
    if m := re.search(r"sid=([a-zA-Z0-9]+)", output):
        sid = m.group(1)
        if sid and sid not in (user, "Guest"):
            return sid, ""
    return None, f"bench={bench_bin} exit={result.returncode}\n{output[:500]}"


@sites_bp.route("/<name>/login", methods=["POST"])
@require_scope(site_name)
def login_to_site(name: str):
    import json

    bench_root = Path(current_app.config["BENCH_ROOT"])
    site_config_path = bench_root / "sites" / name / "site_config.json"
    if not site_config_path.exists():
        return jsonify({"ok": False, "error": "Site not found."}), 404

    sid, debug = _get_site_sid(bench_root, name)
    if not sid:
        return jsonify({"ok": False, "error": "Could not create login session.", "debug": debug})

    from pilot.config.toml_store import BenchTomlStore

    try:
        bench_config = BenchTomlStore.for_bench(bench_root).read()
        http_port = bench_config.http_port
        nginx_enabled = bench_config.production.enabled
    except Exception:
        http_port = 8000
        nginx_enabled = False

    if nginx_enabled:
        try:
            ssl = bool(json.loads(site_config_path.read_text()).get("ssl"))
        except Exception:
            ssl = False
        url = f"{'https' if ssl else 'http'}://{name}/desk?sid={sid}"
    else:
        url = f"http://{name}:{http_port}/desk?sid={sid}"

    return jsonify({"ok": True, "url": url})


@sites_bp.route("/<name>/enable-ssl", methods=["POST"])
@require_scope(site_name)
def enable_ssl(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    config_path = bench_root / "sites" / name / "site_config.json"
    if not config_path.exists():
        return jsonify({"ok": False, "error": "Site not found."}), 404

    from pilot.config.toml_store import BenchTomlStore

    from ..validators import validate_email

    store = BenchTomlStore.for_bench(bench_root)
    # Let's Encrypt needs an ACME account email; persist one if the UI supplied it.
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    if email:
        if err := validate_email(email):
            return jsonify({"ok": False, "error": err, "needs_email": True})
        try:
            with store.edit() as config:
                config.letsencrypt.email = email
        except Exception as e:
            return jsonify({"ok": False, "error": f"Failed to save email: {e}"}), 500
    else:
        try:
            config = store.read()
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

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
    write_private_text(config_path, json.dumps(current, indent=1))

    try:
        task_id = TaskRunner(bench_root).run(
            "setup-letsencrypt",
            {"site": name},
            callbacks={
                "on_failure": {
                    "operation": "disable-site-ssl",
                    "args": {"site": name},
                }
            },
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


def _domain_routes(bench_root: Path):
    from pilot.config.toml_store import BenchTomlStore
    from pilot.core.bench import Bench
    from pilot.core.domain_controller import DomainRouteProvider

    bench = Bench(BenchTomlStore.for_bench(bench_root).read(), bench_root)
    return DomainRouteProvider(bench)


def _apply_domains(bench_root: Path, name: str) -> str:
    """Re-run the right task so nginx (and certs, for SSL sites) pick up the change."""
    import json

    ssl = bool(json.loads((bench_root / "sites" / name / "site_config.json").read_text()).get("ssl"))
    return TaskRunner(bench_root).run("setup-letsencrypt" if ssl else "setup-nginx", {})


@sites_bp.route("/<name>/domains", methods=["GET"])
@require_scope(site_name)
def list_domains(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        routes = _domain_routes(bench_root)
        return jsonify({"domains": routes.domains(name), "primary": routes.primary(name)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@sites_bp.route("/<name>/domains/dns-records", methods=["POST"])
@require_scope(site_name)
def domain_dns_records(name: str):
    """Step 1 of attaching a domain: validate it, return CNAME/A record options."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    domain = ((request.get_json(silent=True) or {}).get("domain") or "").strip()
    if err := validate_site_name(domain):
        return jsonify({"ok": False, "error": err})
    try:
        records = _domain_routes(bench_root).generate_dns_records(name, domain)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "records": records})


@sites_bp.route("/<name>/domains", methods=["POST"])
@require_scope(site_name)
def add_domain(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    domain = ((request.get_json(silent=True) or {}).get("domain") or "").strip()
    if err := validate_site_name(domain):
        return jsonify({"ok": False, "error": err})
    try:
        _domain_routes(bench_root).register(name, domain)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": _apply_domains(bench_root, name)})


@sites_bp.route("/<name>/domains", methods=["DELETE"])
@require_scope(site_name)
def remove_domain(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    domain = ((request.get_json(silent=True) or {}).get("domain") or "").strip()
    try:
        _domain_routes(bench_root).deregister(name, domain)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": _apply_domains(bench_root, name)})


@sites_bp.route("/<name>/domains/primary", methods=["POST"])
@require_scope(site_name)
def set_primary_domain(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    domain = ((request.get_json(silent=True) or {}).get("domain") or "").strip() or None
    try:
        _domain_routes(bench_root).set_primary(name, domain)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    # nginx redirects non-primary hosts to the primary, so regenerate it.
    return jsonify({"ok": True, "task_id": _apply_domains(bench_root, name)})


@sites_bp.route("/<name>/config", methods=["PATCH"])
@require_scope(site_name)
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

    write_private_text(config_path, json.dumps(merged, indent=1))
    return jsonify({"ok": True})


_DEFAULT_BACKUPS_PAGE_SIZE = 20


@sites_bp.route("/<name>/backups")
@require_scope(site_name)
def list_backups(name: str):
    from ..readers.backup_reader import BackupReader

    bench_root = Path(current_app.config["BENCH_ROOT"])
    limit = request.args.get("limit", _DEFAULT_BACKUPS_PAGE_SIZE, type=int)
    try:
        sets = BackupReader(bench_root, name).read_all(limit=limit)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(
        [
            {
                "timestamp": s.timestamp,
                "created_at": s.created_at.isoformat(),
                "is_offsite": s.is_offsite,
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
@require_scope(site_name)
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


@sites_bp.route("/<name>/backups/<timestamp>/offsite-urls")
@require_scope(site_name)
def offsite_backup_urls(name: str, timestamp: str):
    """Pre-signed S3 URLs for a backup run's files — the user downloads
    straight from the bucket, so this server never proxies the transfer."""
    from pilot.config.toml_store import BenchTomlStore
    from pilot.integrations.s3.backups import OffsiteBackup

    bench_root = Path(current_app.config["BENCH_ROOT"])
    config = BenchTomlStore.for_bench(bench_root).read()
    try:
        offsite_backup = OffsiteBackup.from_config(config.s3, bench_root)
        files = offsite_backup.get_backup(name, timestamp)
        if not files:
            return jsonify({"error": f"No offsite backup found for {timestamp}"}), 404
        urls = {
            kind: offsite_backup.presigned_url(name, timestamp, filename)
            for kind, filename in files.items()
        }
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, "urls": urls})


def _backup_cron_command(bench_root: Path, site: str) -> str:
    import sys

    log_file = bench_root / "logs" / f"backup-{site}.log"
    return f"{sys.executable} -m admin.backend.tasks.jobs.backup_site_task {bench_root} {site} --with-files >> {log_file} 2>&1"


def _retention_from_payload(block: dict | None):
    """Build a validated BackupConfig from the UI payload, defaulting to GFS.
    Returns the config, or an error string."""
    from pilot.config.backup_config import VALID_SCHEMES, BackupConfig

    block = block or {}
    config = BackupConfig()
    scheme = str(block.get("scheme", config.scheme)).strip()
    if scheme not in VALID_SCHEMES:
        return f"Retention scheme must be one of: {', '.join(VALID_SCHEMES)}."
    config.scheme = scheme
    for key in config.counts:
        if key not in block:
            continue
        try:
            value = int(block[key])
        except (TypeError, ValueError):
            return f"{key} must be a whole number."
        if value < 0:
            return f"{key} must be zero or more."
        setattr(config, key, value)
    return config


@sites_bp.route("/<name>/backup-schedule", methods=["GET"])
@require_scope(site_name)
def get_backup_schedule(name: str):
    from dataclasses import asdict

    from pilot.config.site_backup_config import read_retention

    from ..cron_manager import CronManager

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        schedule = CronManager(bench_root).get_schedule(name)
        retention = read_retention(bench_root / "sites" / name / "site_config.json")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"schedule": schedule, "retention": asdict(retention) if retention else None})


@sites_bp.route("/<name>/backup-schedule", methods=["POST"])
@require_scope(site_name)
def set_backup_schedule(name: str):
    from pilot.config.site_backup_config import write_retention

    from ..cron_manager import CronManager

    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    schedule = (data.get("schedule") or "").strip()
    if err := validate_cron_expression(schedule):
        return jsonify({"ok": False, "error": err})
    retention = _retention_from_payload(data.get("retention"))
    if isinstance(retention, str):
        return jsonify({"ok": False, "error": retention})
    try:
        CronManager(bench_root).set_schedule(name, schedule, _backup_cron_command(bench_root, name))
        write_retention(bench_root / "sites" / name / "site_config.json", retention)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True})


@sites_bp.route("/<name>/backup-schedule", methods=["DELETE"])
@require_scope(site_name)
def delete_backup_schedule(name: str):
    from pilot.config.site_backup_config import clear_retention

    from ..cron_manager import CronManager

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        CronManager(bench_root).remove_schedule(name)
        clear_retention(bench_root / "sites" / name / "site_config.json")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True})


def _new_site_name_error(bench_root: Path, name: str) -> str | None:
    """Validate a new-site name before any task starts, so the error lands in the UI
    instead of failing mid-run. Mirrors NewSiteCommand._validate."""
    from pilot.config.toml_store import BenchTomlStore
    from pilot.utils import host_owner, normalize_host

    if (bench_root / "sites" / name / "site_config.json").exists():
        return f"Site '{name}' already exists."

    owner = host_owner(bench_root, name)
    if owner:
        return f"'{name}' is already used by bench '{owner}' (as a site or its admin domain). All benches share one nginx, so hostnames must be unique."

    try:
        admin_domain = BenchTomlStore.for_bench(bench_root).read().admin.domain
    except Exception:
        admin_domain = ""
    if admin_domain and normalize_host(name) == normalize_host(admin_domain):
        return f"Site '{name}' clashes with this bench's admin domain. An admin domain must not match a site domain."

    from pilot.core.domain_controller import DomainRouteProvider
    from pilot.utils import matches_wildcard

    patterns = DomainRouteProvider.wildcard_domains()
    if patterns and not matches_wildcard(name, patterns):
        return f"Site name must match one of this bench's wildcard domains: {', '.join(patterns)}."
    return None


def _public_config(config: dict) -> dict:
    """Hide system fields and secret-like keys while preserving custom config."""
    return {
        key: copy.deepcopy(value)
        for key, value in config.items()
        if key not in PROTECTED_CONFIG_KEYS
        and not any(part in key.lower() for part in _SENSITIVE_CONFIG_KEY_PARTS)
    }
