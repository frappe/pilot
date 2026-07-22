from __future__ import annotations

from pathlib import Path

from flask import current_app, jsonify, request

from admin.backend.api.responses import error_response
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import site_name
from admin.backend.middleware import require_scope
from pilot.integrations.central import CentralClient, CentralClientError

_ALLOWED_PREFIXES = ("central.billing.api.billing_api.",)
_ALLOWED_EXACT = frozenset({"central.api.pilot.heartbeat"})


def _is_allowed(method_path: str) -> bool:
    return method_path in _ALLOWED_EXACT or any(method_path.startswith(p) for p in _ALLOWED_PREFIXES)


def _central() -> CentralClient:
    from pilot.config.bench import BenchConfig
    from pilot.core.bench import Bench

    bench_root = Path(current_app.config["BENCH_ROOT"])
    bench = Bench(BenchConfig.read(bench_root), bench_root)
    return CentralClient(bench)


@sites_bp.get("/<name>/central/<path:method_path>")
@sites_bp.post("/<name>/central/<path:method_path>")
@require_scope(site_name)
def central_proxy(name: str, method_path: str):
    if not _is_allowed(method_path):
        return error_response(
            "central_method_forbidden", f"Central method '{method_path}' is not permitted.", 403
        )
    data = request.get_json(silent=True) if request.method == "POST" else None
    try:
        return jsonify(_central().forward(method_path, request.method, data))
    except CentralClientError as exc:
        return error_response("central_unreachable", str(exc), 502)
