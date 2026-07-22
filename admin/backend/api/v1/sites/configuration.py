from __future__ import annotations

from pathlib import Path

from flask import current_app, jsonify, request

from admin.backend.api.responses import error_response
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import (
    internal_error,
    malformed_body,
    site_name,
    site_not_found,
)
from admin.backend.middleware import require_scope
from pilot.core.bench import Bench
from pilot.core.site.config import (
    PROTECTED_CONFIG_KEYS as _PROTECTED_CONFIG_KEYS,
)
from pilot.core.site.config import (
    config_patch_error as _config_patch_error,
)
from pilot.core.site.config import (
    merge_public_config as _merge_public_config,
)
from pilot.core.site.config import (
    public_config as _public_config,
)
from pilot.exceptions import BenchError
from pilot.internal.site_paths import site_config_path

PROTECTED_CONFIG_KEYS = _PROTECTED_CONFIG_KEYS
_CONFIG_ALIASES = (_config_patch_error, _merge_public_config, _public_config)


@sites_bp.get("/<name>/configuration")
@require_scope(site_name)
def get_configuration(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if site_config_path(bench_root, name) is None:
        return site_not_found()
    try:
        return jsonify(Bench(bench_root).site(name).public_config())
    except Exception:
        return internal_error("Could not read site configuration.")


@sites_bp.patch("/<name>/configuration")
@require_scope(site_name)
def update_configuration(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if site_config_path(bench_root, name) is None:
        return site_not_found()

    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        return malformed_body()

    try:
        public = Bench(bench_root).site(name).update_public_config(data)
    except BenchError as error:
        return error_response("protected_configuration", str(error), 422)
    except Exception:
        return internal_error("Could not update site configuration.")
    return jsonify(public)
