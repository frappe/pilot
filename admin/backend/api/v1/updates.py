from __future__ import annotations

import subprocess
from pathlib import Path

from flask import Blueprint, current_app, jsonify

from pilot.loader import cli_root

from ...api.responses import error_response

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


def _app_updates(*, fetch: bool) -> list[dict]:
    bench_root = Path(current_app.config["BENCH_ROOT"])

    from pilot.config.toml_store import BenchTomlStore
    from pilot.core.bench import Bench

    config = BenchTomlStore.for_bench(bench_root).read()
    bench = Bench(config, bench_root)

    apps_info = []
    for app in bench.apps():
        if not app.is_cloned:
            continue
        if fetch:
            _git_fetch(app.path, app.config.branch)
        apps_info.append(_app_info(app))
    return apps_info


def _git_fetch(path: Path, branch: str) -> None:
    try:
        cmd = ["git", "-C", str(path), "fetch", "origin"]
        if branch:
            cmd.append(branch)
        subprocess.run(cmd, capture_output=True, timeout=60)
    except Exception:
        pass


def _app_info(app) -> dict:
    path = app.path
    branch = app.config.branch or _current_branch(path)
    remote_ref = f"origin/{branch}"

    behind = _count(path, f"HEAD..{remote_ref}")
    ahead = _count(path, f"{remote_ref}..HEAD")
    remote_commit = _log_subject(path, remote_ref)
    local_commit = _log_subject(path, "HEAD")

    fetch_head = path / ".git" / "FETCH_HEAD"
    last_fetched = fetch_head.stat().st_mtime if fetch_head.exists() else None

    return {
        "name": app.config.name,
        "branch": branch,
        "commits_behind": behind,
        "commits_ahead": ahead,
        "remote_commit": remote_commit,
        "local_commit": local_commit,
        "last_fetched": last_fetched,
    }


def _current_branch(path: Path) -> str:
    r = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip()


def _count(path: Path, range_: str) -> int:
    r = subprocess.run(
        ["git", "-C", str(path), "rev-list", "--count", range_],
        capture_output=True,
        text=True,
    )
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


def _log_subject(path: Path, ref: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(path), "log", "-1", "--format=%s", ref],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip()


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
    root = cli_root()
    branch = _current_branch(root)
    if fetch:
        _git_fetch(root, branch)

    remote_ref = f"origin/{branch}"
    behind = _count(root, f"HEAD..{remote_ref}")
    remote_commit = _log_subject(root, remote_ref)
    local_commit = _log_subject(root, "HEAD")

    fetch_head = root / ".git" / "FETCH_HEAD"
    last_fetched = fetch_head.stat().st_mtime if fetch_head.exists() else None

    return {
        "branch": branch,
        "commits_behind": behind,
        "update_available": behind > 0,
        "local_commit": local_commit,
        "remote_commit": remote_commit,
        "last_fetched": last_fetched,
    }
