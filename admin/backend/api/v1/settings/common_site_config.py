from __future__ import annotations

from pathlib import Path

from flask import current_app, jsonify, request

from admin.backend.api.responses import error_response
from admin.backend.api.v1.settings import settings_bp
from pilot.core.bench import Bench
from pilot.core.bench.config_files import MANAGED_COMMON_SITE_CONFIG_KEYS
from pilot.exceptions import BenchError, MalformedSiteConfig


@settings_bp.get("/common-site-config")
def get_common_site_config():
    """Bench-wide site settings, with secrets masked and without the keys Pilot manages."""
    bench = Bench(Path(current_app.config["BENCH_ROOT"]))
    try:
        return jsonify(bench.public_common_site_config)
    except MalformedSiteConfig as error:
        return error_response("malformed_common_site_config", str(error), 422)


@settings_bp.get("/common-site-config/managed-keys")
def get_managed_common_site_config_keys():
    """Keys Pilot writes into common_site_config.json itself, which Admin cannot edit."""
    return jsonify(sorted(MANAGED_COMMON_SITE_CONFIG_KEYS))


@settings_bp.patch("/common-site-config")
def update_common_site_config():
    """Merge a patch into common_site_config.json; `null` removes a key."""
    patch = request.get_json(silent=True)
    if not isinstance(patch, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    bench = Bench(Path(current_app.config["BENCH_ROOT"]))
    try:
        return jsonify(bench.update_public_common_site_config(patch))
    except MalformedSiteConfig as error:
        return error_response("malformed_common_site_config", str(error), 422)
    except BenchError as error:
        return error_response("protected_configuration", str(error), 422)
