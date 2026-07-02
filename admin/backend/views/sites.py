from __future__ import annotations

import copy
import secrets
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file

from admin.backend.auth import require_scope
from admin.backend.tasks.callbacks import new_site_failure_callback, ssl_setup_failure_callback
from ..validators import validate_cron_expression, validate_site_name
from admin.backend.tasks.manager.task_runner import TaskRunner

from pilot.platform import is_linux, is_x86_64

from ..readers.app_reader import AppReader
from ..readers.backup_reader import dump_engine
from ..readers.site_reader import SiteReader

site_name = lambda kw: kw["name"]

sites_bp = Blueprint("sites", __name__)

# Confidential / system-managed site_config keys. These are never sent to the
# admin UI and cannot be edited through it — they are preserved as-is on disk.
PROTECTED_CONFIG_KEYS = frozenset({"db_name", "db_password", "db_socket", "db_type", "db_user", "installed_apps", "ssl", "domains", "host_name", "pilot_endpoint", "pilot_auth_token"})


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
    if db_type and db_type not in ("mariadb", "postgres", "sqlite"):
        return jsonify({"ok": False, "error": f"Invalid db_type '{db_type}'."})
    err = validate_site_name(name) or _new_site_name_error(bench_root, name)
    if err:
        return jsonify({"ok": False, "error": err})

    task_args: dict = {"name": name, "admin_password": admin_password}
    if db_type:
        task_args["db_type"] = db_type
    try:
        task_id = TaskRunner(bench_root).run(
            "new-site",
            task_args,
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

    # A MariaDB backup uploaded for a PostgreSQL site must be converted with pgloader.
    from pilot.config.toml_store import BenchTomlStore

    config = BenchTomlStore.for_bench(bench_root).read()
    if config.db_type == "postgres" and dump_engine(db_path) == "mariadb":
        # pgloader only runs on x86_64 Linux — block early rather than fail mid-restore.
        if not (is_linux() and is_x86_64()):
            # Dead end (no resume), so discard the staged upload instead of leaking it.
            shutil.rmtree(upload_dir, ignore_errors=True)
            return jsonify(
                {
                    "ok": False,
                    "error": (
                        "This is a MariaDB backup; restoring it onto a PostgreSQL site needs pgloader, "
                        "which only runs on x86_64 Linux. Convert it on an x86_64 Linux host first, then "
                        "upload the resulting PostgreSQL backup."
                    ),
                }
            )
        # Have the user confirm the conversion (experimental, not guaranteed). The saved
        # paths are returned so the confirm can resume without re-uploading.
        if request.form.get("convert") != "true":
            return jsonify(
                {
                    "ok": False,
                    "needs_conversion_confirm": True,
                    "message": (
                        "You have uploaded a MariaDB backup for a PostgreSQL site restore. "
                        "It will be converted with pgloader before restoring.\n\n"
                        "• This does not work on ARM / Apple Silicon.\n"
                        "• PostgreSQL support is experimental, so a successful conversion is not guaranteed."
                    ),
                    "name": name,
                    "db_file": str(db_path),
                    "public_files": args.get("public_files"),
                    "private_files": args.get("private_files"),
                }
            )
        # Conversion confirmed: mark it so the task adds --force, which the restore
        # needs to stage the MariaDB dump for pgloader.
        args["convert"] = True

    try:
        task_id = TaskRunner(bench_root).run("new-site-from-backup", args)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "task_id": task_id})


@sites_bp.route("/discard-upload", methods=["POST"])
def discard_upload():
    """Delete an uploaded backup that won't be restored (e.g. the user cancelled the
    MariaDB→PostgreSQL conversion prompt), so it doesn't linger in tmp/uploads."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    db_file = (request.get_json(silent=True) or {}).get("db_file") or ""
    uploads_root = (bench_root / "tmp" / "uploads").resolve()
    token_dir = Path(db_file).resolve().parent
    # Only ever remove a token directory directly under this bench's upload staging area.
    if token_dir.parent == uploads_root:
        shutil.rmtree(token_dir, ignore_errors=True)
    return jsonify({"ok": True})


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
    try:
        task_id = TaskRunner(bench_root).run("reinstall-site", {"site": name, "admin_password": "admin"})
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
    try:
        config = store.read()
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
            store.write(config)
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

    config_path.write_text(json.dumps(merged, indent=1))
    return jsonify({"ok": True})


@sites_bp.route("/<name>/backups")
@require_scope(site_name)
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


@sites_bp.route("/<name>/backup-schedule", methods=["GET"])
@require_scope(site_name)
def get_backup_schedule(name: str):
    from ..cron_manager import CronManager

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        schedule = CronManager(bench_root).get_schedule(name)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"schedule": schedule})


@sites_bp.route("/<name>/backup-schedule", methods=["POST"])
@require_scope(site_name)
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
@require_scope(site_name)
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
    """Drop confidential / system-managed keys before exposing site_config."""
    return {k: copy.deepcopy(v) for k, v in config.items() if k not in PROTECTED_CONFIG_KEYS}
