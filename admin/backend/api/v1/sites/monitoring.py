from __future__ import annotations

from typing import TypedDict

from flask import jsonify, request

from admin.backend.api.requests import ApiRequest
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import internal_error, site_name, site_not_found
from admin.backend.middleware import require_scope
from admin.backend.providers.site.monitoring import SiteMonitoringProvider
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
    top_paths: Timeline
    slowest_requests: Timeline
    avg_request_duration: Timeline
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
