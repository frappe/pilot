from __future__ import annotations

import secrets
from pathlib import Path

from flask import current_app, jsonify, request

from admin.backend.api.responses import accepted_task_response, error_response
from admin.backend.api.v1.benches.support import BENCH_NAME_RE, guard_bench_management, target_bench_dir
from admin.backend.api.v1.sites.shared import host_resource_key, task_failure
from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.core.bench.clone_branches import validate_branches
from pilot.core.site.clone import SiteClone
from pilot.exceptions import BenchError, TaskConflictError
from pilot.internal.validators import validate_site_name
from pilot.tasks.clone_bench import CloneBenchTask
from pilot.tasks.clone_site import CloneSiteTask


def clone_branch_options(name: str):
    root = Path(current_app.config["BENCH_ROOT"])
    if not BENCH_NAME_RE.fullmatch(name):
        return error_response("invalid_bench_name", "Invalid bench name.", 422)
    try:
        path = target_bench_dir(root, name)
        if not (path / "bench.toml").is_file():
            return error_response("bench_not_found", "Source bench not found.", 404)
        source = Bench(path)
        return jsonify({"apps": source.get_clone_branch_options()})
    except BenchError as error:
        return error_response("clone_branches_unavailable", str(error), 422)
    except ValueError:
        return error_response("bench_not_found", "Source bench not found.", 404)
    except Exception:
        return error_response("clone_branches_unavailable", "Could not load app branches.", 503)


def clone_bench(name: str):
    root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        return error_response("invalid_clone", "Enter a destination bench name.", 422)
    target = data["name"].strip()
    if not BENCH_NAME_RE.fullmatch(name) or not BENCH_NAME_RE.fullmatch(target):
        return error_response("invalid_clone", "Invalid bench name.", 422)
    try:
        branch = data.get("branch", "default")
        app_branches = data.get("app_branches", {})
        validate_branches(branch, app_branches)
        BenchConfig.default(target).validate()
        source = Bench(target_bench_dir(root, name))
        args = dict(source_bench=source.path.name, name=target, branch=branch, app_branches=app_branches)
        existing = Bench(root).tasks.find_idempotent_task(
            "clone-bench", args, request.headers.get("Idempotency-Key")
        )
        if existing:
            return accepted_task_response(root, existing)
        if target_bench_dir(root, target).exists():
            return error_response("bench_exists", "Destination bench already exists.", 409)
        task_id = CloneBenchTask.queue(
            Bench(root),
            source_bench=source.path.name,
            name=target,
            branch=branch,
            app_branches=app_branches,
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=[f"bench:{name}", f"bench:{target}"],
        )
    except TaskConflictError as error:
        return task_failure(error)
    except BenchError as error:
        return error_response("invalid_clone", str(error), 422)
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(root, task_id)


def clone_site(name: str):
    root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not all(
        isinstance(data.get(key), str) for key in ("name", "target_bench")
    ):
        return error_response("invalid_clone", "Enter a site name and destination bench.", 422)
    target = data["name"].strip()
    bench_name = data["target_bench"].strip()
    if validate_site_name(name) or validate_site_name(target) or not BENCH_NAME_RE.fullmatch(bench_name):
        return error_response("invalid_clone", "Invalid site or bench name.", 422)
    if bench_name != root.name and (response := guard_bench_management()):
        return response
    try:
        bench = Bench(root)
        destination = Bench(target_bench_dir(root, bench_name))
        password = secrets.token_urlsafe(24)
        args = dict(site=name, name=target, target_bench=bench_name, admin_password=password)
        existing = bench.tasks.find_idempotent_task(
            "clone-site", args, request.headers.get("Idempotency-Key")
        )
        if existing:
            return accepted_task_response(root, existing)
        SiteClone(bench.site(name), destination, target, password).validate()
        task_id = CloneSiteTask.queue(
            bench,
            site=name,
            name=target,
            target_bench=bench_name,
            admin_password=password,
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=[f"site:{name}", f"bench:{bench_name}", host_resource_key(target)],
        )
    except TaskConflictError as error:
        return task_failure(error)
    except BenchError as error:
        return error_response("invalid_clone", str(error), 422)
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(root, task_id)
