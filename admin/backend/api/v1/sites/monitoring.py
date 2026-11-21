from __future__ import annotations

from dataclasses import asdict
from typing import TypedDict

from flask import jsonify, request

from admin.backend.api.requests import ApiRequest
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import internal_error, site_name, site_not_found
from admin.backend.middleware import require_scope
from admin.backend.providers.site.monitoring import SiteMonitoringProvider
from pilot.core.bench import Bench
from pilot.internal.site_paths import site_exists


class Timeline(TypedDict):
    """Each point holds its bucket start as `time` plus one value per category."""

    categories: list[str]
    points: list[dict[str, float]]


class SiteAnalytics(TypedDict):
    window: str
    window_seconds: int
    now: int
    requests_over_time: Timeline
    errors_over_time: Timeline
    top_paths: Timeline
    failed_paths: Timeline
    slowest_requests: Timeline
    avg_request_duration: Timeline
    response_time: Timeline
    background_jobs_over_time: Timeline
    top_jobs: Timeline
    slowest_jobs: Timeline
    avg_job_duration: Timeline
    top_ips: Timeline
    slowest_reports: Timeline
    frequent_slow_queries: Timeline
    slowest_queries: Timeline


@sites_bp.get("/<name>/monitoring")
@require_scope(site_name)
def get_monitoring(name: str):
    """Show the site monitoring data."""
    bench_root = ApiRequest.get_bench_root()
    if not site_exists(bench_root, name):
        return site_not_found()
    window = request.args.get("window", "24h")
    try:
        return jsonify(SiteMonitoringProvider(bench_root, name, window).get_analytics())
    except Exception:
        return internal_error("Could not read site monitoring data.")


@sites_bp.get("/<name>/failed-jobs")
@require_scope(site_name)
def get_failed_jobs(name: str):
    """The site's scheduler jobs that failed in the last 24 hours, grouped by job."""
    bench_root = ApiRequest.get_bench_root()
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        jobs = Bench(bench_root).site(name).failed_jobs.get()
    except Exception:
        return internal_error("Could not read failed jobs from the site.")
    return jsonify([asdict(job) for job in jobs])


@sites_bp.get("/<name>/history")
@require_scope(site_name)
def get_site_history(name: str):
    """When the site was created, last updated and each app installed."""
    bench_root = ApiRequest.get_bench_root()
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        history = Bench(bench_root).site(name).history.get()
    except Exception:
        return internal_error("Could not read the site history.")
    return jsonify(asdict(history))
