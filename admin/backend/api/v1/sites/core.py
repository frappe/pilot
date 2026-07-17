from __future__ import annotations

import json
import re
import secrets
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from flask import current_app, jsonify, make_response, request

from pilot.config.bench import BenchConfig
from pilot.config.toml_store import BenchTomlStore
from pilot.internal.site_paths import site_config_path, site_exists
from pilot.internal.validators import validate_site_name
from pilot.tasks.manager.task_runner import TaskRunner
from pilot.utils import normalize_host

from admin.backend.api.responses import accepted_task_response, created_response, error_response
from admin.backend.middleware import rate_limit, require_scope

from admin.backend.providers.apps import AppProvider
from admin.backend.providers.sites import SiteInfo, SiteProvider
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import (
    internal_error,
    invalid_fields,
    malformed_body,
    new_site_name_error,
    site_name,
    site_name_failure,
    site_not_found,
    task_failure,
    text_fields,
)


@sites_bp.get("")
def list_sites():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        sites = SiteProvider(bench_root).get_all()
    except Exception:
        return internal_error("Could not read sites.")

    payload = []
    for site in sites:
        payload.append(_site_resource(site))
    return jsonify(payload)


@sites_bp.route("/<name>")
@require_scope(site_name)
def detail(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        site = SiteProvider(bench_root).get_one(name)
    except Exception:
        return internal_error("Could not read site.")

    # Installable = apps that are cloned but not yet installed on this site
    try:
        all_apps = [a.name for a in AppProvider(bench_root).get_all()]
        installable = [a for a in all_apps if a not in site.installed_apps]
    except Exception:
        installable = []

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


@sites_bp.route("/wildcard-domains", methods=["GET"])
def wildcard_domains():
    """Wildcard domain suffixes (no leading '*') new site names may be built from."""
    from pilot.core.domains import DomainRouteProvider
    from pilot.utils import wildcard_suffix

    try:
        patterns = DomainRouteProvider.wildcard_domains()
    except Exception:
        return internal_error("Could not read wildcard domains.")
    return jsonify({"domains": [wildcard_suffix(p) for p in patterns]})


@sites_bp.post("")
def create_site():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return malformed_body()
    fields = text_fields(data, "name")
    apps_value = data.get("apps", [])
    if fields is None or not isinstance(apps_value, list) or not all(
        isinstance(app, str) for app in apps_value
    ):
        return invalid_fields()

    name = fields["name"]
    admin_password = secrets.token_urlsafe(16)
    apps = [app.strip() for app in apps_value if app.strip()]
    err = validate_site_name(name) or new_site_name_error(bench_root, name)
    if err:
        return site_name_failure(err)

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
        return task_failure(error)

    return accepted_task_response(bench_root, task_id)


@sites_bp.delete("/<name>")
@require_scope(site_name)
def drop_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        task_id = TaskRunner(bench_root).run(
            "drop-site",
            {"site": name},
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.post("/<name>/actions/reinstall")
@require_scope(site_name)
def reinstall_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    elif not isinstance(data, dict):
        return malformed_body()
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
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.post("/<name>/actions/clear-cache")
@require_scope(site_name)
def clear_cache(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        task_id = TaskRunner(bench_root).run(
            "clear-cache",
            {"site": name},
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.post("/<name>/actions/migrate")
@require_scope(site_name)
def migrate_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        task_id = TaskRunner(bench_root).run(
            "migrate",
            {"site": name},
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.post("/<name>/login")
@require_scope(site_name)
@rate_limit(10, 60, user_ip=True)
def create_login_link(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    config_path = site_config_path(bench_root, name)
    if config_path is None:
        return site_not_found()
    try:
        site_config = json.loads(config_path.read_text())
        config = BenchTomlStore.for_bench(bench_root).read()
    except Exception:
        return error_response(
            "configuration_unavailable",
            "Site login configuration is unavailable.",
            503,
        )
    if not isinstance(site_config, dict):
        return error_response(
            "configuration_unavailable",
            "Site login configuration is unavailable.",
            503,
        )

    try:
        sid = create_site_session(bench_root, name)
    except Exception:
        sid = None
    if not sid:
        return error_response(
            "site_login_unavailable",
            "Could not create a site login session.",
            503,
        )

    redirect_url = _login_redirect_url(config, name, site_config)
    url = f"{redirect_url}{'&' if '?' in redirect_url else '?'}sid={sid}"
    return _no_store(created_response({"url": url}, url))


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


def create_site_session(bench_root: Path, site: str) -> str | None:
    python = bench_root / "env" / "bin" / "python"
    program = (
        "import sys, frappe\n"
        "from frappe.auth import CookieManager, LoginManager\n"
        "frappe.init(site=sys.argv[1], sites_path='.')\n"
        "frappe.connect()\n"
        "frappe.utils.set_request(path='/')\n"
        "frappe.local.cookie_manager = CookieManager()\n"
        "frappe.local.login_manager = LoginManager()\n"
        "frappe.local.login_manager.login_as('Administrator')\n"
        "frappe.db.commit()\n"
        "sys.stdout.write(frappe.session.sid)\n"
    )
    result = subprocess.run(
        [str(python), "-c", program, site],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(bench_root / "sites"),
    )
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    sid = lines[-1] if lines else ""
    return sid if re.fullmatch(r"[A-Za-z0-9._-]+", sid) else None


def _login_redirect_url(config: BenchConfig, site: str, site_config: dict) -> str:
    host = _primary_host(site, site_config)
    if not config.production.enabled:
        return _origin("http", host, config.http_port) + "/desk"

    proxy_tls = current_app.config["SESSION_COOKIE_SECURE"] and not config.admin.tls
    secure = proxy_tls or (config.admin.tls and bool(site_config.get("ssl")))
    scheme = "https" if secure else "http"
    port = 443 if proxy_tls else (config.nginx.https_port if secure else config.nginx.http_port)
    return _origin(scheme, host, port) + "/desk"


def _primary_host(site: str, site_config: dict) -> str:
    candidates = {normalize_host(site)}
    for entry in site_config.get("domains") or []:
        domain = entry.get("domain") if isinstance(entry, dict) else entry
        if isinstance(domain, str):
            candidates.add(normalize_host(domain))
    host_name = site_config.get("host_name")
    if isinstance(host_name, str) and host_name.strip():
        parsed = urlsplit(
            host_name if "://" in host_name else f"//{host_name}"
        )
        primary = normalize_host(parsed.hostname or "")
        if primary in candidates:
            return primary
    return normalize_host(site)


def _origin(scheme: str, host: str, port: int) -> str:
    default_port = 443 if scheme == "https" else 80
    suffix = "" if port == default_port else f":{port}"
    return f"{scheme}://{host}{suffix}"


def _no_store(response):
    response = make_response(response)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
