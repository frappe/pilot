from __future__ import annotations

from pathlib import Path

from flask import current_app, jsonify, request

from pilot.exceptions import BenchError
from pilot.integrations.git import GitProviderError, resolve_app_name_from_repo
from pilot.internal.site_paths import site_exists
from pilot.internal.validators import validate_app_name
from pilot.tasks.manager.task_runner import TaskRunner

from admin.backend.api.responses import accepted_task_response, error_response
from admin.backend.middleware import require_scope

from admin.backend.providers.apps import AppProvider
from admin.backend.providers.sites import SiteProvider
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

    try:
        task_id = _submit_install_task(bench_root, name, app, repo, branch)
    except GitProviderError:
        return error_response(
            "invalid_repository", "Could not determine the application name.", 422
        )
    except Exception as error:
        return task_failure(error)
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
        return site_not_found()
    err = validate_app_name(app)
    if err:
        return error_response("invalid_app", err, 422)

    force = request.args.get("force") == "true"
    try:
        task_id = TaskRunner(bench_root).run(
            "uninstall-app", {"site": name, "app": app, "force": force}
        )
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)
