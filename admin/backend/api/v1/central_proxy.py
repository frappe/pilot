from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from admin.backend.auth import require_scope

from pilot.core.central_client import CentralClient, CentralClientError

site_name = lambda kw: kw["name"]

# Transparent, allowlisted proxy: a site calls `sites/<site>/central/<method>` and the pilot
# forwards it to Central with its X-Pilot-Token (Central resolves team/asset from the credential).
central_proxy_bp = Blueprint("central_proxy", __name__)

# A site may reach only Central's billing facade + the heartbeat probe. config/enroll are
# excluded — those are the pilot's own boot-time calls, not something a site should trigger.
_ALLOWED_PREFIXES = ("central.billing.api.billing_api.",)
_ALLOWED_EXACT = frozenset({"central.api.pilot.heartbeat"})


def _is_allowed(method_path: str) -> bool:
    return method_path in _ALLOWED_EXACT or any(method_path.startswith(p) for p in _ALLOWED_PREFIXES)


def _central() -> CentralClient:
    from pilot.config.toml_store import BenchTomlStore
    from pilot.core.bench import Bench

    bench_root = Path(current_app.config["BENCH_ROOT"])
    bench = Bench(BenchTomlStore.for_bench(bench_root).read(), bench_root)
    return CentralClient(bench)


@central_proxy_bp.route("/<name>/central/<path:method_path>", methods=["GET", "POST"])
@require_scope(site_name)
def proxy(name: str, method_path: str):
    if not _is_allowed(method_path):
        return jsonify({"error": f"Central method '{method_path}' is not permitted."}), 403
    data = request.get_json(silent=True) if request.method == "POST" else None
    try:
        return jsonify(_central().forward(method_path, request.method, data))
    except CentralClientError as exc:
        return jsonify({"error": str(exc)}), 502
