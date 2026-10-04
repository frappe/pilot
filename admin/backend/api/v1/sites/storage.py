from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from flask import current_app, jsonify, request

from admin.backend.api.responses import accepted_task_response
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import (
    internal_error,
    site_name,
    site_not_found,
    task_failure,
)
from admin.backend.middleware import require_scope
from pilot.core.bench import Bench
from pilot.internal.site_paths import site_exists
from pilot.tasks.refresh_storage_usage import RefreshStorageUsageTask


@sites_bp.get("/storage")
def get_storage():
    """Every site's files and database usage, from the report the pilot-storage
    timer refreshes. Measured here only when there is no report yet."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        report = Bench(bench_root).site_storage.get_report()
    except Exception:
        return internal_error("Could not read site storage usage.")
    return jsonify(asdict(report))


@sites_bp.get("/<name>/storage")
@require_scope(site_name)
def get_site_storage(name: str):
    """One site's folder breakdown plus its database size, for callers holding only its token."""
    from admin.backend.providers.storage import StorageProvider

    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        report = Bench(bench_root).site_storage.get_report()
        files = StorageProvider(bench_root).get_site(name)
    except Exception:
        return internal_error("Could not read site storage usage.")
    usage = next((site for site in report.sites if site.name == name), None)
    return jsonify(
        {
            "collected_at": report.collected_at,
            "database_bytes": usage.database_bytes if usage else 0,
            **asdict(files),
        }
    )


@sites_bp.post("/<name>/actions/refresh-storage")
@require_scope(site_name)
def refresh_storage(name: str):
    """Measuring walks every site directory, so it runs as a task. One report
    covers the bench, and the resource key folds concurrent clicks into it."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        task_id = RefreshStorageUsageTask.queue(
            Bench(bench_root),
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key="site-storage",
        )
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)
