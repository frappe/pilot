from __future__ import annotations

import subprocess
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

updates_bp = Blueprint("updates", __name__)


def _cli_root() -> Path:
    import bench_cli as _pkg
    return Path(_pkg.__file__).parent.parent


@updates_bp.route("/")
def get_updates():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    do_fetch = request.args.get("fetch") == "1"

    from bench_cli.config.bench_config import BenchConfig
    from bench_cli.core.bench import Bench

    config = BenchConfig.from_file(bench_root / "bench.toml")
    bench = Bench(config, bench_root)

    apps_info = []
    for app in bench.apps():
        if not app.is_cloned:
            continue
        if do_fetch:
            _git_fetch(app.path, app.config.branch)
        apps_info.append(_app_info(app))

    return jsonify({"apps": apps_info})


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


@updates_bp.route("/cli")
def get_cli_update():
    cli_root = _cli_root()
    do_fetch = request.args.get("fetch") == "1"

    branch = _current_branch(cli_root)
    if do_fetch:
        _git_fetch(cli_root, branch)

    remote_ref = f"origin/{branch}"
    behind = _count(cli_root, f"HEAD..{remote_ref}")
    remote_commit = _log_subject(cli_root, remote_ref)
    local_commit = _log_subject(cli_root, "HEAD")

    fetch_head = cli_root / ".git" / "FETCH_HEAD"
    last_fetched = fetch_head.stat().st_mtime if fetch_head.exists() else None

    return jsonify({
        "branch": branch,
        "commits_behind": behind,
        "update_available": behind > 0,
        "local_commit": local_commit,
        "remote_commit": remote_commit,
        "last_fetched": last_fetched,
    })
