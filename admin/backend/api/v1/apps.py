from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from admin.backend.api.responses import accepted_task_response, error_response
from admin.backend.providers.apps import AppProvider
from pilot.core.bench import Bench
from pilot.internal.git import GitRepo
from pilot.internal.validators import validate_app_name, validate_repo_url
from pilot.tasks.fetch_app_updates import FetchAppUpdatesTask
from pilot.tasks.get_and_install_app import GetAndInstallAppTask
from pilot.tasks.get_app import GetAppTask
from pilot.tasks.create_app import CreateAppTask
from pilot.tasks.remove_app import RemoveAppTask

apps_bp = Blueprint("apps", __name__)
marketplace_bp = Blueprint("marketplace", __name__)


@apps_bp.get("")
def index():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        apps = AppProvider(bench_root).get_all()
    except Exception:
        return error_response("apps_unavailable", "Could not read installed apps.", 500)
    return jsonify([asdict(a) for a in apps])


@marketplace_bp.get("/apps")
def marketplace():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        from pilot.integrations.marketplace import Marketplace

        apps = Marketplace(Bench(bench_root)).read_all_apps()
        return jsonify([a.to_dict() for a in apps])
    except Exception:
        return error_response("marketplace_unavailable", "Could not read marketplace apps.", 500)


@apps_bp.post("")
def install():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)

    task_args, sites, response = _install_request(data, bench_root)
    if response is not None:
        return response

    try:
        task_id = _queue_install_task(bench_root, task_args, sites)
    except Exception:
        return error_response("app_install_failed", "Could not start app installation.", 500)

    return accepted_task_response(bench_root, task_id)


def _install_request(data, bench_root: Path):
    if not isinstance(data, dict):
        return {}, [], error_response("malformed_request", "Expected a JSON object.", 400)
    if any(
        value is not None and not isinstance(value, str)
        for value in (data.get("name"), data.get("repo"), data.get("branch"))
    ):
        return {}, [], error_response("invalid_app", "App fields must be strings.", 422)

    sites = data.get("sites", [])
    if not isinstance(sites, list) or any(not isinstance(s, str) for s in sites):
        return {}, [], error_response("invalid_sites", "sites must be a list of strings.", 422)
    sites = list(dict.fromkeys(sites))

    name = (data.get("name") or "").strip()
    repo = (data.get("repo") or "").strip()
    branch = (data.get("branch") or "").strip()

    if repo:
        err = validate_repo_url(repo)
        if err:
            return {}, [], error_response("invalid_app", err, 422)
        task_args = {"name": name or repo, "repo": repo, "branch": branch}
    else:
        err = validate_app_name(name)
        if err:
            return {}, [], error_response("invalid_app", err, 422)
        if (bench_root / "apps" / name / ".git").exists():
            return (
                {},
                [],
                error_response("app_already_installed", f"'{name}' is already installed.", 409),
            )
        task_args = {"name": name, "marketplace_app": name}

    return task_args, sites, None


def _queue_install_task(bench_root: Path, task_args: dict, sites: list[str]) -> str:
    bench = Bench(bench_root)
    if sites:
        return GetAndInstallAppTask.queue(
            bench,
            repo=task_args.get("repo", ""),
            branch=task_args.get("branch", ""),
            marketplace_app=task_args.get("marketplace_app", ""),
            sites=sites,
        )

    return GetAppTask.queue(
        bench,
        name=task_args["name"],
        repo=task_args.get("repo", ""),
        branch=task_args.get("branch", ""),
        marketplace_app=task_args.get("marketplace_app", ""),
    )


@apps_bp.post("/create")
def create_app():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
        
    name = (data.get("name") or "").strip()
    if not name:
        return error_response("invalid_app", "App name is required.", 422)
        
    err = validate_app_name(name)
    if err:
        return error_response("invalid_app", err, 422)
        
    if (bench_root / "apps" / name).exists():
        return error_response("app_already_exists", f"'{name}' already exists in bench.", 409)
        
    task_args = {
        "name": name,
        "title": (data.get("title") or "").strip(),
        "description": (data.get("description") or "").strip(),
        "publisher": (data.get("publisher") or "").strip(),
        "email": (data.get("email") or "").strip(),
        "app_license": (data.get("app_license") or "mit").strip(),
        "create_github_repo": bool(data.get("create_github_repo", False)),
        "github_repo_private": bool(data.get("github_repo_private", False)),
        "sites": sites if isinstance(sites := (data.get("sites") or []), list) else []
    }
    
    try:
        bench = Bench(bench_root)
        task_id = CreateAppTask.queue(
            bench,
            name=task_args["name"],
            title=task_args["title"],
            description=task_args["description"],
            publisher=task_args["publisher"],
            email=task_args["email"],
            app_license=task_args["app_license"],
            create_github_repo=task_args["create_github_repo"],
            github_repo_private=task_args["github_repo_private"],
            sites=task_args["sites"]
        )
    except Exception as e:
        return error_response("app_creation_failed", f"Could not start app creation: {e}", 500)
        
    return accepted_task_response(bench_root, task_id)



@apps_bp.get("/<name>")
def detail(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not (bench_root / "apps" / name / ".git").exists():
        return error_response("app_not_found", f"App '{name}' not found in bench.", 404)
    try:
        app = AppProvider(bench_root).get_app(name)
    except Exception:
        return error_response("apps_unavailable", "Could not read the app.", 500)
    return jsonify(asdict(app))


@apps_bp.patch("/<name>")
def update(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    if data.get("repo") is not None and not isinstance(data["repo"], str):
        return error_response("invalid_repository", "Repository URL must be a string.", 422)
    repo = (data.get("repo") or "").strip()

    err = validate_repo_url(repo)
    if err:
        return error_response("invalid_repository", err, 422)

    app_path = bench_root / "apps" / name
    if not (app_path / ".git").exists():
        return error_response("app_not_found", f"App '{name}' not found.", 404)

    if not GitRepo(app_path).set_remote_url(repo):
        return error_response("upstream_update_failed", "Could not update the app upstream.", 500)

    try:
        app = AppProvider(bench_root).get_app(name)
    except Exception:
        return error_response("apps_unavailable", "Could not read the app.", 500)
    return jsonify(asdict(app))


@apps_bp.delete("/<name>")
def remove(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])

    if not (bench_root / "apps" / name).exists():
        return error_response("app_not_found", f"App '{name}' not found in bench.", 404)

    try:
        task_id = RemoveAppTask.queue(Bench(bench_root), name=name)
    except Exception:
        return error_response("app_removal_failed", "Could not start app removal.", 500)

    return accepted_task_response(bench_root, task_id)


@apps_bp.post("/fetch")
def fetch_updates():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        task_id = FetchAppUpdatesTask.queue(Bench(bench_root))
    except Exception:
        return error_response("app_fetch_failed", "Could not start fetching app updates.", 500)
    return accepted_task_response(bench_root, task_id)
