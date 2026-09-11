from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify

from admin.backend.api.responses import error_response
from admin.backend.providers.marketplace_updates import MarketplaceGitHubUpdates
from pilot.core.bench import Bench
from pilot.exceptions import RegistryUnavailableError
from pilot.internal.git import GitRepo
from pilot.utils import cli_root

updates_bp = Blueprint("updates", __name__)


@updates_bp.get("/app-updates")
def get_app_updates():
    try:
        return jsonify({"apps": _app_updates(fetch=False)})
    except Exception:
        return error_response("app_updates_unavailable", "Could not read app updates.", 500)


@updates_bp.post("/app-update-checks")
def check_app_updates():
    try:
        return jsonify({"apps": _app_updates(fetch=True)})
    except Exception:
        return error_response("app_update_check_failed", "Could not check for app updates.", 500)


@updates_bp.get("/marketplace-github-updates")
def get_marketplace_github_updates():
    try:
        return jsonify(_marketplace_github_updates(refresh=False))
    except RegistryUnavailableError as exc:
        return error_response("registry_unavailable", str(exc), 503)
    except Exception:
        return error_response(
            "marketplace_github_updates_unavailable",
            "Could not read marketplace GitHub updates.",
            500,
        )


@updates_bp.post("/marketplace-github-update-checks")
def check_marketplace_github_updates():
    try:
        return jsonify(_marketplace_github_updates(refresh=True))
    except RegistryUnavailableError as exc:
        return error_response("registry_unavailable", str(exc), 503)
    except Exception:
        return error_response(
            "marketplace_github_update_check_failed",
            "Could not check marketplace GitHub updates.",
            500,
        )


def _marketplace_github_updates(*, refresh: bool) -> dict:
    checker = MarketplaceGitHubUpdates(cli_root())
    return checker.check(refresh=refresh)


def _app_updates(*, fetch: bool) -> list[dict]:
    bench_root = Path(current_app.config["BENCH_ROOT"])
    bench = Bench(bench_root)

    apps_info = []
    for app in bench.apps():
        if not app.is_cloned:
            continue
        repo = GitRepo(app.path)
        branch = app.config.branch or repo.branch
        if fetch:
            repo.fetch(branch, timeout=60)
        apps_info.append(_app_info(app.config.name, branch, repo))
    return apps_info


def _app_info(name: str, branch: str, repo: GitRepo) -> dict:
    remote_ref = f"origin/{branch}"
    return {
        "name": name,
        "branch": branch,
        "commits_behind": repo.count(f"HEAD..{remote_ref}"),
        "commits_ahead": repo.count(f"{remote_ref}..HEAD"),
        "remote_commit": repo.commit_subject(remote_ref),
        "local_commit": repo.commit_subject("HEAD"),
        "last_fetched": repo.last_fetched,
    }


@updates_bp.get("/cli-updates")
def get_cli_update():
    try:
        return jsonify(_cli_update(fetch=False))
    except Exception:
        return error_response("cli_update_unavailable", "Could not read CLI update status.", 500)


@updates_bp.post("/cli-update-checks")
def check_cli_update():
    try:
        return jsonify(_cli_update(fetch=True))
    except Exception:
        return error_response("cli_update_check_failed", "Could not check for a CLI update.", 500)


def _cli_update(*, fetch: bool) -> dict:
    import pilot

    if pilot.is_dev_build:
        return _cli_update_dev(fetch=fetch)
    return _cli_update_release(fetch=fetch)


def _cli_update_dev(*, fetch: bool) -> dict:
    repo = GitRepo(cli_root())
    branch = repo.branch
    if fetch:
        repo.fetch(branch, timeout=60)

    behind = repo.count(f"HEAD..origin/{branch}")
    return {
        "current_version": "dev",
        "is_dev": True,
        "branch": branch,
        "commits_behind": behind,
        "update_available": behind > 0,
        "local_commit": repo.commit_subject("HEAD"),
        "remote_commit": repo.commit_subject(f"origin/{branch}"),
        "last_fetched": repo.last_fetched,
    }


def _cli_update_release(*, fetch: bool) -> dict:
    import pilot

    result = {
        "current_version": pilot.__version__,
        "is_dev": False,
        "update_available": False,
        "latest_version": None,
    }
    if fetch:
        from pilot.updater import update_available

        available, latest = update_available()
        result["update_available"] = available
        result["latest_version"] = latest
    return result
