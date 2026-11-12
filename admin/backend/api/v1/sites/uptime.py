from __future__ import annotations

from typing import TypedDict

from flask import jsonify, request

from admin.backend.api.requests import ApiRequest
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import internal_error, site_name, site_not_found
from admin.backend.middleware import require_scope
from admin.backend.providers.site_uptime import SiteUptimeProvider
from pilot.internal.site_paths import site_exists


class UptimeBucket(TypedDict):
    time: int
    percent: float | None
    checks: int


class SiteUptime(TypedDict):
    window: str
    window_seconds: int
    bucket_seconds: int
    now: int
    overall_percent: float | None
    buckets: list[UptimeBucket]
    production_enabled: bool


@sites_bp.get("/<name>/uptime")
@require_scope(site_name)
def get_uptime(name: str):
    bench_root = ApiRequest.get_bench_root()
    if not site_exists(bench_root, name):
        return site_not_found()
    window = request.args.get("window", "1h")
    try:
        return jsonify(SiteUptimeProvider(bench_root, name, window).get_uptime())
    except Exception:
        return internal_error("Could not read site uptime data.")
