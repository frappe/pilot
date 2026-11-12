from __future__ import annotations

from flask import jsonify

from admin.backend.api.requests import ApiRequest
from admin.backend.api.responses import error_response
from admin.backend.api.v1.settings import settings_bp
from pilot.core.bench.config_files import MANAGED_COMMON_SITE_CONFIG_KEYS
from pilot.exceptions import BenchError, MalformedSiteConfig


@settings_bp.get("/common-site-config")
def get_common_site_config():
    """Bench-wide site settings, with secrets masked and without the keys Pilot manages."""
    bench = ApiRequest.get_bench()
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
    patch = ApiRequest.get_json_body()
    bench = ApiRequest.get_bench()
    try:
        return jsonify(bench.update_public_common_site_config(patch))
    except MalformedSiteConfig as error:
        return error_response("malformed_common_site_config", str(error), 422)
    except BenchError as error:
        return error_response("protected_configuration", str(error), 422)
