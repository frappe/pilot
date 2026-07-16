from __future__ import annotations

import copy
import json
import re
import secrets
import shutil
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file

from pilot.integrations.git import GitProviderError, resolve_app_name_from_repo
from pilot.exceptions import (
    BenchError,
    ConfigError,
    DomainConflictError,
    DomainProviderError,
    TaskConflictError,
)
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked

from admin.backend.api.responses import error_response, no_content_response
from admin.backend.security.authentication import require_scope
from admin.backend.site_paths import site_config_path, site_exists
from admin.backend.task_response import accepted_task_response
from admin.backend.uploads import (
    UploadError,
    create_upload_directory,
    save_archive_upload,
    save_database_upload,
)
from ...security.validation import validate_app_name, validate_cron_expression, validate_site_name
from pilot.tasks.manager.task_runner import TaskCallback, TaskRunner

from ...readers.apps import AppReader
from ...readers.sites import SiteInfo, SiteReader


def site_name(kwargs: dict) -> str:
    return kwargs["name"]


sites_bp = Blueprint("sites", __name__)
site_restores_bp = Blueprint("site-restores", __name__)

# Confidential / system-managed site_config keys. These are never sent to the
# admin UI and cannot be edited through it — they are preserved as-is on disk.
PROTECTED_CONFIG_KEYS = frozenset(
    {
        "backup_retention",
        "db_host",
        "db_name",
        "db_password",
        "db_port",
        "db_socket",
        "db_type",
        "db_user",
        "domains",
        "host_name",
        "installed_apps",
        "pilot_auth_token",
        "pilot_endpoint",
        "ssl",
    }
)
_SENSITIVE_CONFIG_KEY_PARTS = (
    "_key",
    "access_key",
    "api_key",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "dsn",
    "password",
    "private_key",
    "secret",
    "session_id",
    "token",
)


@sites_bp.get("")
def list_sites():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        sites = SiteReader(bench_root).read_all()
    except Exception:
        return _internal_error("Could not read sites.")

    payload = []
    for site in sites:
        payload.append(_site_resource(site))
    return jsonify(payload)


@sites_bp.route("/<name>")
@require_scope(site_name)
def detail(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return _site_not_found()
    try:
        site = SiteReader(bench_root).read_one(name)
    except Exception:
        return _internal_error("Could not read site.")

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

    return jsonify(
        {
            **_site_resource(site),
            "ssl": bool(site.site_config.get("ssl")),
            "installable_apps": installable,
            "http_port": http_port,
            "nginx_enabled": nginx_enabled,
            "admin_tls": admin_tls,
        }
    )


@sites_bp.get("/<name>/apps")
@require_scope(site_name)
def site_apps(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return _site_not_found()
    try:
        site = SiteReader(bench_root).read_one(name)
    except Exception:
        return _internal_error("Could not read site apps.")

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
    from pilot.core.domains import DomainRouteProvider
    from pilot.utils import wildcard_suffix

    try:
        patterns = DomainRouteProvider.wildcard_domains()
    except Exception:
        return _internal_error("Could not read wildcard domains.")
    return jsonify({"domains": [wildcard_suffix(p) for p in patterns]})


@sites_bp.post("")
def create_site():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _malformed_body()
    fields = _text_fields(data, "name")
    apps_value = data.get("apps", [])
    if fields is None or not isinstance(apps_value, list) or not all(
        isinstance(app, str) for app in apps_value
    ):
        return _invalid_fields()

    name = fields["name"]
    admin_password = secrets.token_urlsafe(16)
    apps = [app.strip() for app in apps_value if app.strip()]
    err = validate_site_name(name) or _new_site_name_error(bench_root, name)
    if err:
        return _site_name_failure(err)

    task_args: dict = {"name": name, "admin_password": admin_password}
    if apps:
        task_args["apps"] = apps
    cleanup_callback = {
        "operation": "remove-failed-site",
        "args": {"site": name},
    }
    try:
        task_id = TaskRunner(bench_root).run(
            "new-site",
            task_args,
            callbacks={
                "on_failure": cleanup_callback,
                "on_cancel": cleanup_callback,
            },
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return _task_failure(error)

    return accepted_task_response(bench_root, task_id)


@site_restores_bp.post("/site-restores")
def create_site_restore():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    name = (request.form.get("name") or "").strip()
    admin_password = request.form.get("admin_password")
    if not isinstance(admin_password, str) or not admin_password.strip():
        admin_password = secrets.token_urlsafe(16)
    err = validate_site_name(name) or _new_site_name_error(bench_root, name)
    if err:
        return _site_name_failure(err)

    db_upload = request.files.get("db_file")
    if not db_upload:
        return error_response(
            "missing_database_backup", "Database backup file is required.", 422
        )

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
    except UploadError:
        if upload_dir:
            shutil.rmtree(upload_dir, ignore_errors=True)
        return error_response("invalid_upload", "The uploaded backup is invalid.", 422)
    except OSError:
        if upload_dir:
            shutil.rmtree(upload_dir, ignore_errors=True)
        return _internal_error("Could not store the uploaded backup.")

    try:
        submission = TaskRunner(bench_root).submit(
            "new-site-from-backup",
            args,
            callbacks={
                "on_success": {
                    "operation": "cleanup-site-restore",
                    "args": {
                        "upload_dir": str(upload_dir),
                        "site": name,
                        "remove_site": False,
                    },
                },
                "on_failure": {
                    "operation": "cleanup-site-restore",
                    "args": {
                        "upload_dir": str(upload_dir),
                        "site": name,
                        "remove_site": True,
                    },
                },
                "on_cancel": {
                    "operation": "cleanup-site-restore",
                    "args": {
                        "upload_dir": str(upload_dir),
                        "site": name,
                        "remove_site": True,
                    },
                },
            },
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        shutil.rmtree(upload_dir, ignore_errors=True)
        return _task_failure(error)
    if not submission.created:
        shutil.rmtree(upload_dir, ignore_errors=True)
    return accepted_task_response(bench_root, submission.task_id)


@sites_bp.delete("/<name>")
@require_scope(site_name)
def drop_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return _site_not_found()
    try:
        task_id = TaskRunner(bench_root).run(
            "drop-site",
            {"site": name},
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return _task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.post("/<name>/actions/reinstall")
@require_scope(site_name)
def reinstall_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return _site_not_found()
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    elif not isinstance(data, dict):
        return _malformed_body()
    admin_password = data.get("admin_password")
    if not isinstance(admin_password, str) or not admin_password.strip():
        admin_password = secrets.token_urlsafe(16)
    try:
        task_id = TaskRunner(bench_root).run(
            "reinstall-site",
            {"site": name, "admin_password": admin_password},
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return _task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.post("/<name>/backups")
@require_scope(site_name)
def backup_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return _site_not_found()
    try:
        task_id = TaskRunner(bench_root).run("backup-site", {"site": name, "with_files": True})
    except Exception as error:
        return _task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.post("/<name>/actions/clear-cache")
@require_scope(site_name)
def clear_cache(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return _site_not_found()
    try:
        task_id = TaskRunner(bench_root).run(
            "clear-cache",
            {"site": name},
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return _task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.post("/<name>/actions/migrate")
@require_scope(site_name)
def migrate_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return _site_not_found()
    try:
        task_id = TaskRunner(bench_root).run(
            "migrate",
            {"site": name},
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return _task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.post("/<name>/apps")
@require_scope(site_name)
def install_site_app(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return _site_not_found()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _malformed_body()
    fields = _text_fields(data, "app", "repo", "branch")
    if fields is None:
        return _invalid_fields()
    app, repo, branch = fields["app"], fields["repo"], fields["branch"]
    if not app and not repo:
        return error_response("missing_app", "App name or repository is required.", 422)

    try:
        task_id = _submit_install_task(bench_root, name, app, repo, branch)
    except GitProviderError:
        return error_response(
            "invalid_repository", "Could not determine the application name.", 422
        )
    except Exception as error:
        return _task_failure(error)
    return accepted_task_response(bench_root, task_id)


def _submit_install_task(
    bench_root: Path, site: str, app: str, repo: str, branch: str
) -> str:
    """An app already cloned into the bench installs directly; otherwise it is
    fetched first, by repository URL or by marketplace name."""
    runner = TaskRunner(bench_root)
    if app and _is_app_cloned(bench_root, app):
        return runner.run("install-app", {"site": site, "app": app})
    if repo:
        app = app or resolve_app_name_from_repo(bench_root, repo, branch)["name"]
        task_args = {"site": site, "app": app, "repo": repo}
        if branch:
            task_args["branch"] = branch
        return runner.run("get-and-install-app", task_args)
    return runner.run(
        "get-and-install-app", {"site": site, "app": app, "marketplace_app": app}
    )


def _is_app_cloned(bench_root: Path, app: str) -> bool:
    from pilot.config.toml_store import BenchTomlStore
    from pilot.core.bench import Bench

    bench = Bench(BenchTomlStore.for_bench(bench_root).read(), bench_root)
    try:
        return bench.app(app).is_cloned
    except BenchError:
        return False


@sites_bp.delete("/<name>/apps/<app>")
@require_scope(site_name)
def delete_site_app(name: str, app: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return _site_not_found()
    err = validate_app_name(app)
    if err:
        return error_response("invalid_app", err, 422)

    force = request.args.get("force") == "true"
    try:
        task_id = TaskRunner(bench_root).run(
            "uninstall-app", {"site": name, "app": app, "force": force}
        )
    except Exception as error:
        return _task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.post("/<name>/actions/enable-tls")
@require_scope(site_name)
def enable_tls(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    config_path = site_config_path(bench_root, name)
    if config_path is None:
        return _site_not_found()

    from pilot.config.toml_store import BenchTomlStore

    from ...security.validation import validate_email

    store = BenchTomlStore.for_bench(bench_root)
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    elif not isinstance(data, dict):
        return _malformed_body()
    fields = _text_fields(data, "email")
    if fields is None:
        return _invalid_fields()
    email = fields["email"]
    if email:
        if err := validate_email(email):
            return error_response(
                "invalid_email", err, 422, {"needs_email": True}
            )
    else:
        try:
            config = store.read()
        except Exception:
            return _internal_error("Could not read certificate configuration.")

    if not email and not config.letsencrypt.email:
        return error_response(
            "missing_certificate_email",
            "A Let's Encrypt account email is required to issue certificates.",
            422,
            {"needs_email": True},
        )

    try:
        current = json.loads(config_path.read_text())
    except Exception:
        return _internal_error("Could not read the site configuration.")
    if not isinstance(current, dict):
        return _internal_error("Could not read the site configuration.")
    if current.get("ssl"):
        return error_response("tls_already_enabled", "TLS is already enabled.", 409)

    rollback: TaskCallback = {"operation": "disable-site-ssl", "args": {"site": name}}
    task_args = {"site": name}
    if email:
        task_args["email"] = email
    try:
        task_id = TaskRunner(bench_root).run(
            "setup-letsencrypt",
            task_args,
            callbacks={
                "on_failure": rollback,
                "on_cancel": rollback,
            },
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return _task_failure(error)
    return accepted_task_response(bench_root, task_id)


def _domain_routes(bench_root: Path):
    from pilot.config.toml_store import BenchTomlStore
    from pilot.core.bench import Bench
    from pilot.core.domains import DomainRouteProvider

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
    if not site_exists(bench_root, name):
        return _site_not_found()
    try:
        routes = _domain_routes(bench_root)
        return jsonify({"domains": routes.domains(name), "primary": routes.primary(name)})
    except BenchError as error:
        return _domain_failure(error, "Could not read site domains.")
    except Exception:
        return _internal_error("Could not read site domains.")


@sites_bp.get("/<name>/domains/<domain>/dns-records")
@require_scope(site_name)
def domain_dns_records(name: str, domain: str):
    """Read-only guidance for attaching a domain: CNAME/A record options."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return _site_not_found()
    if err := validate_site_name(domain):
        return error_response("invalid_domain", err, 422)
    try:
        records = _domain_routes(bench_root).generate_dns_records(name, domain)
    except BenchError as error:
        return _domain_failure(error, "Could not generate DNS records.")
    except Exception:
        return _internal_error("Could not generate DNS records.")
    return jsonify(records)


@sites_bp.post("/<name>/domains")
@require_scope(site_name)
def add_domain(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return _site_not_found()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _malformed_body()
    fields = _text_fields(data, "domain")
    if fields is None:
        return _invalid_fields()
    domain = fields["domain"]
    if err := validate_site_name(domain):
        return error_response("invalid_domain", err, 422)
    try:
        _domain_routes(bench_root).register(name, domain)
        task_id = _apply_domains(bench_root, name)
    except BenchError as error:
        return _domain_failure(error, "Could not attach the domain.")
    except Exception:
        return _internal_error("Could not attach the domain.")
    return accepted_task_response(bench_root, task_id)


@sites_bp.get("/<name>/domains/<domain>")
@require_scope(site_name)
def get_domain(name: str, domain: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return _site_not_found()
    if err := validate_site_name(domain):
        return error_response("invalid_domain", err, 422)
    try:
        attached, is_primary = _domain_status(_domain_routes(bench_root), name, domain)
    except BenchError as error:
        return _domain_failure(error, "Could not read the domain.")
    except Exception:
        return _internal_error("Could not read the domain.")
    if not attached:
        return error_response("domain_not_found", "Domain not found.", 404)
    return jsonify({"domain": domain, "is_primary": is_primary})


@sites_bp.patch("/<name>/domains/<domain>")
@require_scope(site_name)
def update_domain(name: str, domain: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return _site_not_found()
    if err := validate_site_name(domain):
        return error_response("invalid_domain", err, 422)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("primary") is not True:
        return error_response(
            "invalid_fields", 'Only setting {"primary": true} is supported.', 422
        )
    try:
        _domain_routes(bench_root).set_primary(name, domain)
        task_id = _apply_domains(bench_root, name)
    except BenchError as error:
        return _domain_failure(error, "Could not change the primary domain.")
    except Exception:
        return _internal_error("Could not change the primary domain.")
    # nginx redirects non-primary hosts to the primary, so regenerate it.
    return accepted_task_response(bench_root, task_id)


@sites_bp.delete("/<name>/domains/<domain>")
@require_scope(site_name)
def remove_domain(name: str, domain: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return _site_not_found()
    if err := validate_site_name(domain):
        return error_response("invalid_domain", err, 422)
    try:
        _domain_routes(bench_root).deregister(name, domain)
        task_id = _apply_domains(bench_root, name)
    except BenchError as error:
        return _domain_failure(error, "Could not detach the domain.")
    except Exception:
        return _internal_error("Could not detach the domain.")
    return accepted_task_response(bench_root, task_id)


def _domain_status(routes, site_name: str, domain: str) -> tuple[bool, bool]:
    from pilot.utils import normalize_host

    normalized = normalize_host(domain)
    primary = routes.primary(site_name)
    if normalized == normalize_host(site_name):
        return True, not primary or normalize_host(primary) == normalized
    attached = normalized in {normalize_host(d) for d in routes.domains(site_name)}
    return attached, bool(primary) and normalize_host(primary) == normalized


@sites_bp.get("/<name>/configuration")
@require_scope(site_name)
def get_configuration(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    config_path = site_config_path(bench_root, name)
    if config_path is None:
        return _site_not_found()
    try:
        config = json.loads(config_path.read_text())
    except Exception:
        return _internal_error("Could not read site configuration.")
    if not isinstance(config, dict):
        return _internal_error("Could not read site configuration.")
    return jsonify(_public_config(config))


@sites_bp.patch("/<name>/configuration")
@require_scope(site_name)
def update_configuration(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    config_path = site_config_path(bench_root, name)
    if config_path is None:
        return _site_not_found()

    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        return _malformed_body()

    try:
        with exclusive_file_lock(config_path):
            current = json.loads(config_path.read_text())
            if not isinstance(current, dict):
                raise ValueError("Site configuration must be a JSON object.")
            error = _config_patch_error(current, data)
            if error:
                return error_response("protected_configuration", error, 422)
            merged = _merge_public_config(current, data)
            replace_private_text_locked(config_path, json.dumps(merged, indent=1))
    except Exception:
        return _internal_error("Could not update site configuration.")
    return jsonify(_public_config(merged))


_DEFAULT_BACKUPS_PAGE_SIZE = 20


@sites_bp.get("/<name>/backups")
@require_scope(site_name)
def list_backups(name: str):
    from ...readers.backups import BackupReader

    bench_root = Path(current_app.config["BENCH_ROOT"])
    limit = request.args.get("limit", _DEFAULT_BACKUPS_PAGE_SIZE, type=int)
    try:
        sets = BackupReader(bench_root, name).read_all(limit=limit)
    except Exception:
        return _internal_error("Could not read site backups.")
    return jsonify([_backup_set_resource(s) for s in sets])


@sites_bp.get("/<name>/backups/<timestamp>")
@require_scope(site_name)
def get_backup(name: str, timestamp: str):
    from ...readers.backups import BackupReader

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        sets = BackupReader(bench_root, name).read_all()
    except Exception:
        return _internal_error("Could not read site backups.")
    match = next((s for s in sets if s.timestamp == timestamp), None)
    if match is None:
        return error_response("backup_not_found", "Backup not found.", 404)
    return jsonify(_backup_set_resource(match))


def _backup_set_resource(s) -> dict:
    return {
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


@sites_bp.get("/<name>/backups/<timestamp>/files/<file_id>/content")
@require_scope(site_name)
def download_backup_file(name: str, timestamp: str, file_id: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not file_id.startswith(timestamp) or "/" in file_id or "\\" in file_id or file_id.startswith("."):
        return error_response("invalid_filename", "Backup filename is invalid.", 422)

    backups_dir = (bench_root / "sites" / name / "private" / "backups").resolve()
    target = (backups_dir / file_id).resolve()
    if backups_dir not in target.parents or not target.is_file():
        return error_response("backup_not_found", "Backup file not found.", 404)

    return send_file(target, as_attachment=True, download_name=file_id)


@sites_bp.get("/<name>/backups/<timestamp>/download-links")
@require_scope(site_name)
def backup_download_links(name: str, timestamp: str):
    """Pre-signed S3 URLs for a backup run's files — the user downloads
    straight from the bucket, so this server never proxies the transfer."""
    from pilot.config.toml_store import BenchTomlStore
    from pilot.integrations.s3.backups import OffsiteBackup

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        config = BenchTomlStore.for_bench(bench_root).read()
        offsite_backup = OffsiteBackup.from_config(config.s3, bench_root)
        files = offsite_backup.get_backup(name, timestamp)
        if not files:
            return error_response(
                "backup_not_found", "Offsite backup not found.", 404
            )
        links = {
            kind: offsite_backup.presigned_url(name, timestamp, filename)
            for kind, filename in files.items()
        }
    except Exception:
        return _internal_error("Could not create offsite backup URLs.")

    return jsonify(links)


def _backup_cron_command(bench_root: Path, site: str) -> str:
    import sys

    log_file = bench_root / "logs" / f"backup-{site}.log"
    return f"{sys.executable} -m pilot.tasks.jobs.backup_site_task {bench_root} {site} --with-files >> {log_file} 2>&1"


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


@sites_bp.get("/<name>/backup-schedule")
@require_scope(site_name)
def get_backup_schedule(name: str):
    from dataclasses import asdict

    from pilot.config.site_backup_config import read_retention

    from ...cron_manager import CronManager

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        schedule = CronManager(bench_root).get_schedule(name)
        retention = read_retention(bench_root / "sites" / name / "site_config.json")
    except Exception:
        return _internal_error("Could not read the backup schedule.")
    return jsonify({"schedule": schedule, "retention": asdict(retention) if retention else None})


@sites_bp.put("/<name>/backup-schedule")
@require_scope(site_name)
def set_backup_schedule(name: str):
    from pilot.config.site_backup_config import write_retention

    from ...cron_manager import CronManager

    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _malformed_body()
    fields = _text_fields(data, "schedule")
    retention_value = data.get("retention")
    if fields is None or (
        retention_value is not None and not isinstance(retention_value, dict)
    ):
        return _invalid_fields()
    schedule = fields["schedule"]
    if err := validate_cron_expression(schedule):
        return error_response("invalid_schedule", err, 422)
    retention = _retention_from_payload(retention_value)
    if isinstance(retention, str):
        return error_response("invalid_retention", retention, 422)
    try:
        CronManager(bench_root).set_schedule(name, schedule, _backup_cron_command(bench_root, name))
        write_retention(bench_root / "sites" / name / "site_config.json", retention)
    except Exception:
        return _internal_error("Could not update the backup schedule.")
    return get_backup_schedule(name)


@sites_bp.delete("/<name>/backup-schedule")
@require_scope(site_name)
def delete_backup_schedule(name: str):
    from pilot.config.site_backup_config import clear_retention

    from ...cron_manager import CronManager

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        CronManager(bench_root).remove_schedule(name)
        clear_retention(bench_root / "sites" / name / "site_config.json")
    except Exception:
        return _internal_error("Could not remove the backup schedule.")
    return no_content_response()


def _site_resource(site: SiteInfo) -> dict:
    framework_branch = site.site_config.get("frappe_branch", "")
    return {
        "name": site.name,
        "exists": site.exists,
        "installed_apps": [
            app for app in site.installed_apps if isinstance(app, str)
        ],
        "framework_branch": framework_branch if isinstance(framework_branch, str) else "",
        "broken": site.broken,
        "provisioning": site.provisioning,
    }


def _site_not_found():
    return error_response("site_not_found", "Site not found.", 404)


def _malformed_body():
    return error_response("malformed_body", "Request body must be a JSON object.", 400)


def _invalid_fields():
    return error_response(
        "invalid_fields", "One or more request fields are invalid.", 422
    )


def _text_fields(data: dict, *names: str) -> dict[str, str] | None:
    fields = {}
    for name in names:
        value = data.get(name, "")
        if not isinstance(value, str):
            return None
        fields[name] = value.strip()
    return fields


def _internal_error(message: str):
    return error_response("internal_error", message, 500)


def _task_failure(error: Exception):
    if isinstance(error, TaskConflictError):
        return error_response(
            "task_conflict", "A conflicting task is already active.", 409
        )
    if isinstance(error, ValueError):
        return error_response("invalid_task", str(error), 422)
    return _internal_error("Could not start the requested task.")


def _domain_conflict():
    return error_response(
        "domain_conflict", "The domain conflicts with the current site state.", 409
    )


def _domain_failure(error: BenchError, message: str):
    if isinstance(error, ConfigError):
        raise error
    if isinstance(error, DomainConflictError):
        return _domain_conflict()
    if isinstance(error, DomainProviderError):
        return error_response(
            "domain_provider_unavailable",
            "The domain provider is unavailable.",
            503,
        )
    return _internal_error(message)


def _site_name_failure(message: str):
    if "already" in message or "clashes" in message:
        return error_response(
            "site_name_conflict", "The site name is already in use.", 409
        )
    return error_response("invalid_site_name", message, 422)


def _new_site_name_error(bench_root: Path, name: str) -> str | None:
    """Validate a new-site name before any task starts, so the error lands in the UI
    instead of failing mid-run. Mirrors NewSiteCommand._validate."""
    from pilot.config.toml_store import BenchTomlStore
    from pilot.utils import host_owner, normalize_host

    sites_path = bench_root / "sites"
    if sites_path.is_symlink():
        return "Sites directory must not be a symbolic link."
    site_path = sites_path / name
    if site_path.is_symlink() or (site_path / "site_config.json").exists():
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

    from pilot.core.domains import DomainRouteProvider
    from pilot.utils import matches_wildcard

    patterns = DomainRouteProvider.wildcard_domains()
    if patterns and not matches_wildcard(name, patterns):
        return f"Site name must match one of this bench's wildcard domains: {', '.join(patterns)}."
    return None


def _public_config(config: dict) -> dict:
    """Hide system fields and secret-like keys while preserving custom config."""
    return {
        key: _public_config_value(value)
        for key, value in config.items()
        if _is_public_config_key(key)
    }


def _public_config_value(value):
    if isinstance(value, dict):
        return _public_config(value)
    if isinstance(value, list):
        return [_public_config_value(item) for item in value]
    return copy.deepcopy(value)


def _merge_public_config(current: dict, submitted: dict) -> dict:
    merged = copy.deepcopy(current)
    for key, submitted_value in submitted.items():
        if submitted_value is None:
            merged.pop(key, None)
            continue
        current_value = current.get(key)
        merged[key] = _merge_public_value(current_value, submitted_value)
    return merged


def _merge_public_value(current, submitted):
    if isinstance(current, dict) and isinstance(submitted, dict):
        return _merge_public_config(current, submitted)
    return copy.deepcopy(submitted)


def _config_patch_error(current, submitted) -> str | None:
    if not isinstance(submitted, dict):
        return "Configuration patches must be JSON objects."
    for key, value in submitted.items():
        if not isinstance(key, str) or not _is_public_config_key(key):
            return "System-managed and secret-like configuration keys cannot be changed."
        existing = current.get(key) if isinstance(current, dict) else None
        if value is None:
            if _contains_protected_config(existing):
                return "A configuration value containing protected fields cannot be removed."
            continue
        if isinstance(value, dict):
            error = _config_patch_error(existing if isinstance(existing, dict) else {}, value)
            if error:
                return error
        elif isinstance(value, list):
            if _contains_protected_config(existing):
                return "A list containing protected fields cannot be replaced."
            for item in value:
                error = _submitted_config_value_error(item)
                if error:
                    return error
        elif _contains_protected_config(existing):
            return "A configuration value containing protected fields cannot change type."
    return None


def _submitted_config_value_error(value) -> str | None:
    if isinstance(value, dict):
        return _config_patch_error({}, value)
    if isinstance(value, list):
        for item in value:
            if error := _submitted_config_value_error(item):
                return error
    return None


def _contains_protected_config(value) -> bool:
    if isinstance(value, dict):
        return any(
            not _is_public_config_key(key) or _contains_protected_config(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_protected_config(child) for child in value)
    return False


def _is_public_config_key(key: str) -> bool:
    normalized = re.sub(
        r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])",
        "_",
        key,
    ).lower().replace("-", "_")
    compact = normalized.replace("_", "")
    compact_secret_parts = (
        "accesskey",
        "apikey",
        "encryptionkey",
        "privatekey",
        "secretkey",
    )
    return (
        normalized not in PROTECTED_CONFIG_KEYS
        and normalized != "key"
        and not any(part in compact for part in compact_secret_parts)
        and not any(part in normalized for part in _SENSITIVE_CONFIG_KEY_PARTS)
    )
