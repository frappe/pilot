from __future__ import annotations

import socket
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from admin.backend.api.responses import error_response
from admin.backend.api.v1.benches.support import ADMIN_DOMAIN_RE, guard_bench_management
from admin.backend.providers.bench import BenchProvider

bench_readiness_bp = Blueprint("bench-readiness", __name__)
bench_readiness_bp.before_request(guard_bench_management)


@bench_readiness_bp.post("/bench-readiness-checks")
def create_readiness_check():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    if "domain" in data and not isinstance(data["domain"], str):
        return error_response("invalid_domain", "domain must be a string.", 422)
    if "scheme" in data and not isinstance(data["scheme"], str):
        return error_response("invalid_scheme", "scheme must be a string.", 422)

    domain = (data.get("domain") or "").strip()
    if domain:
        return _domain_readiness_response(bench_root, data, domain)
    if "scheme" in data:
        return error_response(
            "invalid_readiness_check",
            "scheme is valid only with domain.",
            422,
        )
    return _port_readiness_response(data.get("port"))


def _domain_readiness_response(bench_root: Path, data: dict, domain: str):
    if "port" in data:
        return error_response(
            "invalid_readiness_check",
            "Provide either domain or port, not both.",
            422,
        )
    if not ADMIN_DOMAIN_RE.fullmatch(domain):
        return error_response("invalid_domain", "domain must be a valid hostname.", 422)
    scheme = (data.get("scheme") or "http").strip()
    if scheme not in ("http", "https"):
        return error_response("invalid_scheme", "scheme must be 'http' or 'https'.", 422)
    return jsonify({"ready": BenchProvider(bench_root).is_wizard_ready(domain, scheme)})


def _port_readiness_response(port):
    if isinstance(port, bool) or not isinstance(port, int):
        return error_response("invalid_port", "port must be an integer.", 422)
    if not 1 <= port <= 65535:
        return error_response("invalid_port", "port must be between 1 and 65535.", 422)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            pass
        return jsonify({"ready": True})
    except OSError:
        return jsonify({"ready": False})
