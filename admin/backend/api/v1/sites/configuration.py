from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from flask import current_app, jsonify, request

from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
from pilot.internal.site_paths import site_config_path

from admin.backend.api.responses import error_response
from admin.backend.middleware import require_scope

from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import internal_error, malformed_body, site_name, site_not_found

# Confidential / system-managed site_config keys. These are never sent to the
# admin UI and cannot be edited through it — they are preserved as-is on disk.
PROTECTED_CONFIG_KEYS = frozenset(
    {
        "backup_retention",
        "db_host",
        "db_name",
        "db_password",
        "db_port",
        "db_socket",
        "db_type",
        "db_user",
        "domains",
        "host_name",
        "installed_apps",
        "pilot_auth_token",
        "pilot_endpoint",
        "ssl",
    }
)
_SENSITIVE_CONFIG_KEY_PARTS = (
    "_key",
    "access_key",
    "api_key",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "dsn",
    "password",
    "private_key",
    "secret",
    "session_id",
    "token",
)


@sites_bp.get("/<name>/configuration")
@require_scope(site_name)
def get_configuration(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    config_path = site_config_path(bench_root, name)
    if config_path is None:
        return site_not_found()
    try:
        config = json.loads(config_path.read_text())
    except Exception:
        return internal_error("Could not read site configuration.")
    if not isinstance(config, dict):
        return internal_error("Could not read site configuration.")
    return jsonify(_public_config(config))


@sites_bp.patch("/<name>/configuration")
@require_scope(site_name)
def update_configuration(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    config_path = site_config_path(bench_root, name)
    if config_path is None:
        return site_not_found()

    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        return malformed_body()

    try:
        with exclusive_file_lock(config_path):
            current = json.loads(config_path.read_text())
            if not isinstance(current, dict):
                raise ValueError("Site configuration must be a JSON object.")
            error = _config_patch_error(current, data)
            if error:
                return error_response("protected_configuration", error, 422)
            merged = _merge_public_config(current, data)
            replace_private_text_locked(config_path, json.dumps(merged, indent=1))
    except Exception:
        return internal_error("Could not update site configuration.")
    return jsonify(_public_config(merged))


def _public_config(config: dict) -> dict:
    """Hide system fields and secret-like keys while preserving custom config."""
    return {
        key: _public_config_value(value)
        for key, value in config.items()
        if _is_public_config_key(key)
    }


def _public_config_value(value):
    if isinstance(value, dict):
        return _public_config(value)
    if isinstance(value, list):
        return [_public_config_value(item) for item in value]
    return copy.deepcopy(value)


def _merge_public_config(current: dict, submitted: dict) -> dict:
    merged = copy.deepcopy(current)
    for key, submitted_value in submitted.items():
        if submitted_value is None:
            merged.pop(key, None)
            continue
        current_value = current.get(key)
        merged[key] = _merge_public_value(current_value, submitted_value)
    return merged


def _merge_public_value(current, submitted):
    if isinstance(current, dict) and isinstance(submitted, dict):
        return _merge_public_config(current, submitted)
    return copy.deepcopy(submitted)


def _config_patch_error(current, submitted) -> str | None:
    if not isinstance(submitted, dict):
        return "Configuration patches must be JSON objects."
    for key, value in submitted.items():
        if not isinstance(key, str) or not _is_public_config_key(key):
            return "System-managed and secret-like configuration keys cannot be changed."
        existing = current.get(key) if isinstance(current, dict) else None
        if value is None:
            if _contains_protected_config(existing):
                return "A configuration value containing protected fields cannot be removed."
            continue
        if isinstance(value, dict):
            error = _config_patch_error(existing if isinstance(existing, dict) else {}, value)
            if error:
                return error
        elif isinstance(value, list):
            if _contains_protected_config(existing):
                return "A list containing protected fields cannot be replaced."
            for item in value:
                error = _submitted_config_value_error(item)
                if error:
                    return error
        elif _contains_protected_config(existing):
            return "A configuration value containing protected fields cannot change type."
    return None


def _submitted_config_value_error(value) -> str | None:
    if isinstance(value, dict):
        return _config_patch_error({}, value)
    if isinstance(value, list):
        for item in value:
            if error := _submitted_config_value_error(item):
                return error
    return None


def _contains_protected_config(value) -> bool:
    if isinstance(value, dict):
        return any(
            not _is_public_config_key(key) or _contains_protected_config(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_protected_config(child) for child in value)
    return False


def _is_public_config_key(key: str) -> bool:
    normalized = re.sub(
        r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])",
        "_",
        key,
    ).lower().replace("-", "_")
    compact = normalized.replace("_", "")
    compact_secret_parts = (
        "accesskey",
        "apikey",
        "encryptionkey",
        "privatekey",
        "secretkey",
    )
    return (
        normalized not in PROTECTED_CONFIG_KEYS
        and normalized != "key"
        and not any(part in compact for part in compact_secret_parts)
        and not any(part in normalized for part in _SENSITIVE_CONFIG_KEY_PARTS)
    )
