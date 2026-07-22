from __future__ import annotations

from pathlib import Path

from flask import current_app, jsonify, request

from admin.backend.api.responses import accepted_task_response, error_response
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import (
    internal_error,
    invalid_fields,
    malformed_body,
    site_name,
    site_not_found,
    task_failure,
    text_fields,
)
from admin.backend.middleware import require_scope
from admin.backend.providers.apps import AppProvider
from admin.backend.providers.sites import SiteProvider
from pilot.core.bench import Bench
from pilot.exceptions import BenchError
from pilot.internal.site_paths import site_exists
from pilot.internal.validators import validate_app_name, validate_repo_url
from pilot.tasks.get_and_install_app import GetAndInstallAppTask
from pilot.tasks.install_app import InstallAppTask
from pilot.tasks.uninstall_app import UninstallAppTask


@sites_bp.get("/<name>/apps")
@require_scope(site_name)
def site_apps(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        site = SiteProvider(bench_root).get_one(name)
    except Exception:
        return internal_error("Could not read site apps.")

    provider = AppProvider(bench_root)
    result = []
    for app_name in site.installed_apps:
        try:
            info = provider.get_app(app_name)
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


@sites_bp.post("/<name>/apps")
@require_scope(site_name)
def install_site_app(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return malformed_body()
    fields = text_fields(data, "app", "repo", "branch")
    if fields is None:
        return invalid_fields()
    app, repo, branch = fields["app"], fields["repo"], fields["branch"]
    if not app and not repo:
        return error_response("missing_app", "App name or repository is required.", 422)
    if repo and (err := validate_repo_url(repo)):
        return error_response("invalid_repository", err, 422)

    try:
        task_id = _submit_install_task(bench_root, name, app, repo, branch)
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)


def _submit_install_task(bench_root: Path, site: str, app: str, repo: str, branch: str) -> str:
    """An app already cloned into the bench installs directly; otherwise it is
    fetched first, by repository URL or by marketplace name."""
    bench = Bench(bench_root)
    if app and _is_app_cloned(bench_root, app):
        return InstallAppTask.queue(bench, site=site, app=app)
    if repo:
        return GetAndInstallAppTask.queue(bench, repo=repo, branch=branch, site=site)
    return GetAndInstallAppTask.queue(bench, marketplace_app=app, site=site)


def _is_app_cloned(bench_root: Path, app: str) -> bool:
    try:
        return Bench(bench_root).app(app).is_cloned
    except BenchError:
        return False


@sites_bp.delete("/<name>/apps/<app>")
@require_scope(site_name)
def delete_site_app(name: str, app: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    err = validate_app_name(app)
    if err:
        return error_response("invalid_app", err, 422)

    force = request.args.get("force") == "true"
    try:
        task_id = UninstallAppTask.queue(Bench(bench_root), site=name, app=app, force=force)
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)
