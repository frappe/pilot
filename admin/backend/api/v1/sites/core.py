from __future__ import annotations

import secrets
from pathlib import Path

from flask import current_app, jsonify, request, url_for

from admin.backend.api.responses import (
    accepted_response,
    accepted_task_response,
    created_response,
    error_response,
)
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.login import no_store as _no_store
from admin.backend.api.v1.sites.login import unreachable_host_hint
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
from admin.backend.middleware import is_bench_scoped, rate_limit, require_scope
from admin.backend.providers.apps import AppProvider
from admin.backend.providers.sites import SiteInfo, SiteProvider
from pilot.core.bench import Bench
from pilot.core.site.login import site_url
from pilot.internal.site_paths import site_config_path, site_exists
from pilot.internal.validators import validate_site_name
from pilot.tasks.clear_cache import ClearCacheTask
from pilot.tasks.drop_site import DropSiteTask
from pilot.tasks.new_site import NewSiteTask
from pilot.tasks.reinstall_site import ReinstallSiteTask
from pilot.tasks.rename_site import RenameSiteTask


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
        installable = [a for a in all_apps if a not in site.active_apps]
    except Exception:
        installable = []

    try:
        bench_config = Bench(bench_root).config
        http_port = bench_config.http_port
        nginx_enabled = bench_config.production.enabled
        admin_tls = bench_config.admin.tls
        url = site_url(name, site.site_config, bench_config)
    except Exception:
        http_port = 8000
        nginx_enabled = False
        admin_tls = False
        url = f"http://{name}:8000"

    return jsonify(
        {
            **_site_resource(site),
            "ssl": bool(site.site_config.get("ssl")),
            "installable_apps": installable,
            "http_port": http_port,
            "nginx_enabled": nginx_enabled,
            "admin_tls": admin_tls,
            "url": url,
        }
    )


@sites_bp.route("/wildcard-domains", methods=["GET"])
def wildcard_domains():
    """Wildcard domain suffixes (no leading '*') new site names may be built from."""
    from pilot.core.adapters.domain_provider import DomainRouteProvider
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
    if (
        fields is None
        or not isinstance(apps_value, list)
        or not all(isinstance(app, str) for app in apps_value)
    ):
        return invalid_fields()

    name = fields["name"]
    admin_password = secrets.token_urlsafe(16)
    apps = [app.strip() for app in apps_value if app.strip()]
    err = validate_site_name(name) or new_site_name_error(bench_root, name)
    if err:
        return site_name_failure(err)

    try:
        task_id = NewSiteTask.queue(
            Bench(bench_root),
            name=name,
            admin_password=admin_password,
            apps=apps,
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
        task_id = DropSiteTask.queue(
            Bench(bench_root),
            site=name,
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.post("/<name>/actions/rename")
@require_scope(site_name)
def rename_site(name: str):
    """Queue a rename of `name` to the body's `new_name`, validated like a new site."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    if not is_bench_scoped():
        # A site's own token must not rename its site - the token is bound to the
        # hostname it would be destroying. Renaming is a bench administration action.
        return error_response(
            "bench_scope_required",
            "Renaming a site needs a bench session.",
            403,
        )
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return malformed_body()
    fields = text_fields(data, "new_name")
    if fields is None:
        return invalid_fields()

    new_name = fields["new_name"]
    err = validate_site_name(new_name) or new_site_name_error(bench_root, new_name)
    if err:
        return site_name_failure(err)
    try:
        task_id = RenameSiteTask.queue(
            Bench(bench_root),
            site=name,
            new_name=new_name,
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=[f"site:{name.lower()}", f"site:{new_name.lower()}"],
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
        task_id = ReinstallSiteTask.queue(
            Bench(bench_root),
            site=name,
            admin_password=admin_password,
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
        task_id = ClearCacheTask.queue(
            Bench(bench_root),
            site=name,
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
    bench = Bench(bench_root)
    operation = bench.migrations.create_site_migrate(name)
    try:
        task_id = operation.begin()
    except Exception as error:
        bench.migrations.delete(operation.id)
        return task_failure(error)
    return accepted_response(
        {"operation_id": operation.id, "task_id": task_id},
        url_for("migrations.get_migration", operation_id=operation.id),
    )


@sites_bp.post("/<name>/login")
@require_scope(site_name)
@rate_limit(10, 60, user_ip=True)
def create_login_link(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    config_path = site_config_path(bench_root, name)
    if config_path is None:
        return site_not_found()
    try:
        bench = Bench(bench_root)
        proxy_tls = current_app.config["SESSION_COOKIE_SECURE"] and not bench.config.admin.tls
        url = bench.site(name).admin_login_url(proxy_tls=proxy_tls)
    except Exception:
        return error_response(
            "configuration_unavailable",
            "Site login configuration is unavailable.",
            503,
        )
    if not url:
        return error_response(
            "site_login_unavailable",
            "Could not create a site login session.",
            503,
        )

    payload = {"url": url}
    if hint := unreachable_host_hint(url):
        payload["hint"] = hint
    return _no_store(created_response(payload, url))


def _site_resource(site: SiteInfo) -> dict:
    framework_branch = site.site_config.get("frappe_branch", "")
    return {
        "name": site.name,
        "exists": site.exists,
        "active_apps": [app for app in site.active_apps if isinstance(app, str)],
        "framework_branch": framework_branch if isinstance(framework_branch, str) else "",
        "broken": site.broken,
        "provisioning": site.provisioning,
        "setup_complete": site.setup_complete,
    }
