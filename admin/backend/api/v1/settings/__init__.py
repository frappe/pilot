from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from admin.backend.api.responses import error_response
from admin.backend.api.v1.settings.config import ConfigPatcher
from admin.backend.middleware import client_ip
from pilot.config import (
    WAF_MODES,
    WAF_RULE_ACTIONS,
    WAF_RULE_FIELDS,
    WAF_RULE_MATCH,
    WAF_RULE_OPERATORS,
    BenchConfig,
)
from pilot.core.bench import Bench
from pilot.core.bench.settings import (
    SettingsApplyFailed,
    firewall_payload,
    is_restart_needed,
    llm_payload,
    restart_trigger_values,
    s3_payload,
    waf_payload,
    worker_groups_payload,
)
from pilot.integrations.llm import clear_system_prompt, read_system_prompt, write_system_prompt
from pilot.managers.platform import is_linux, native_process_manager
from pilot.managers.redis import RedisManager
from pilot.managers.waf import WafManager

settings_bp = Blueprint("settings", __name__)
audit_bp = Blueprint("audit", __name__)
network_bp = Blueprint("network", __name__)

__all__ = [
    "ConfigPatcher",
    "audit_bp",
    "build_settings_response",
    "firewall_payload",
    "is_restart_needed",
    "llm_payload",
    "network_bp",
    "restart_trigger_values",
    "s3_payload",
    "settings_bp",
    "waf_payload",
]


class _SettingsUpdateRejected(Exception):
    pass


def build_settings_response(config: BenchConfig, bench_root: Path | None = None) -> dict:
    return {
        "is_linux": is_linux(),
        "native_process_manager": native_process_manager(),
        "bench": {
            "name": config.name,
            "python": config.python_version,
            "http_port": config.http_port,
            "socketio_port": config.socketio_port,
            "default_branch": config.default_branch,
            "db_type": config.db_type,
            "allow_developer_mode": config.allow_developer_mode,
        },
        "mariadb": {
            "host": config.mariadb.host,
            "port": config.mariadb.port,
            "admin_user": config.mariadb.admin_user,
            "socket_path": config.mariadb.socket_path,
        },
        "postgres": {
            "host": config.postgres.host,
            "port": config.postgres.port,
            "admin_user": config.postgres.admin_user,
            "password_set": bool(config.postgres.root_password),
        },
        "redis": {
            "cache_port": config.redis.cache_port,
            "queue_port": config.redis.queue_port,
            "version": RedisManager.installed_version() or config.redis.version or "",
        },
        "workers": worker_groups_payload(config),
        "firewall": firewall_payload(config),
        "waf": {
            **waf_payload(config),
            "installed": WafManager.is_installed(),
            "modes": list(WAF_MODES),
            "rule_fields": list(WAF_RULE_FIELDS),
            "rule_operators": list(WAF_RULE_OPERATORS),
            "rule_actions": list(WAF_RULE_ACTIONS),
            "rule_match": list(WAF_RULE_MATCH),
        },
        "production": {
            "process_manager": config.production.process_manager or "none",
            "enabled": config.production.enabled,
        },
        "admin": {
            "domain": config.admin.domain,
            "tls": config.admin.tls,
        },
        "letsencrypt": {"email": config.letsencrypt.email},
        "s3": s3_payload(config),
        "s3_providers": s3_provider_options(),
        "llm": {
            **llm_payload(config),
            "system_prompt": read_system_prompt(bench_root) if bench_root else "",
        },
        "llm_providers": llm_provider_options(),
        "monitor": {
            "system_log_path": str(config.monitor.system_log_path),
            "log_path": str(config.monitor.log_path) if config.monitor.log_path else "",
            "system_log_max_size": config.monitor.system_log_max_size,
            "application_log_max_size": config.monitor.application_log_max_size,
        },
    }


def s3_provider_options() -> list[dict]:
    from pilot.integrations.s3.base import PROVIDER_LABELS, SUPPORTED_REGIONS

    return [
        {"value": provider, "label": PROVIDER_LABELS[provider], "regions": regions}
        for provider, regions in SUPPORTED_REGIONS.items()
    ]


def llm_provider_options() -> list[dict]:
    from pilot.integrations.llm.registry import provider_options

    return provider_options()


@settings_bp.get("/llm/models")
def llm_models():
    """Models litellm knows for a provider — powers the model combobox."""
    from pilot.integrations.llm.registry import models_for

    provider = request.args.get("provider", "").strip()
    if not provider:
        return jsonify([])
    return jsonify(models_for(provider))


@settings_bp.get("")
def get_settings():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        config = BenchConfig.read(bench_root)
    except Exception:
        return error_response("settings_unavailable", "Could not read settings.", 500)
    return jsonify(build_settings_response(config, bench_root))


_AUDIT_LOG_DEFAULT_LIMIT = 50
_AUDIT_LOG_MAX_LIMIT = 500


@audit_bp.get("/audit-events")
def audit_log():
    """Return filtered bench audit events, newest first."""
    from admin.backend.api.responses import paginated_response, parse_pagination
    from pilot.core.bench.audit_log import AuditLog

    bench_root = Path(current_app.config["BENCH_ROOT"])
    limit, offset = parse_pagination(_AUDIT_LOG_DEFAULT_LIMIT, _AUDIT_LOG_MAX_LIMIT)
    try:
        log = AuditLog(Bench(bench_root))

        def fetch_newest(count: int) -> list:
            return log.entries(
                entry_type=request.args.get("type") or None,
                site=request.args.get("site") or None,
                status=request.args.get("status") or None,
                limit=count,
            )

        return paginated_response(fetch_newest, limit, offset)
    except Exception:
        return error_response("audit_unavailable", "Could not read audit events.", 500)


@network_bp.get("/network/client")
def my_ip():
    """Return the client IP the firewall should allow-list."""
    return jsonify({"ip": client_ip(default="")})


@settings_bp.patch("")
def update_settings():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)

    try:
        update = _save_settings_update(bench_root, data)
    except _SettingsUpdateRejected as error:
        return error_response("invalid_settings", str(error), 422)
    except Exception:
        return error_response("settings_update_failed", "Could not update settings.", 500)

    try:
        restarted, waf_warning = apply_post_save_changes(
            bench_root,
            update["config"],
            update["old_restart"],
            update["old_firewall"],
            update["old_waf"],
            update["old_s3_config"],
        )
    except SettingsApplyFailed as error:
        return error_response(error.code, error.message, 500, {"saved": True})

    return jsonify(_settings_update_result(restarted, waf_warning))


def _save_settings_update(bench_root: Path, data: dict) -> dict:
    with BenchConfig.open(bench_root) as config:
        old_restart = restart_trigger_values(config)
        old_firewall = firewall_payload(config)
        old_waf = waf_payload(config)
        old_s3_config = s3_payload(config)

        if error := ConfigPatcher(config, data).apply():
            raise _SettingsUpdateRejected(error)
        _verify_s3_update(config, old_s3_config)

    _apply_system_prompt(bench_root, data.get("llm") or {})

    return {
        "config": config,
        "old_restart": old_restart,
        "old_firewall": old_firewall,
        "old_waf": old_waf,
        "old_s3_config": old_s3_config,
    }


def _apply_system_prompt(bench_root: Path, llm: dict) -> None:
    """Persist the system prompt to its sidecar file (not bench.toml)."""
    if llm.get("disconnect"):
        clear_system_prompt(bench_root)
    elif "system_prompt" in llm:
        write_system_prompt(bench_root, str(llm["system_prompt"]))


def _verify_s3_update(config: BenchConfig, old_s3_config: dict) -> None:
    if s3_payload(config) == old_s3_config or not config.s3.access_key:
        return

    from pilot.integrations.s3.base import S3, S3IntegrationError

    try:
        S3.from_config(config.s3)
    except S3IntegrationError as error:
        raise _SettingsUpdateRejected(str(error)) from error


def _settings_update_result(restarted: bool, waf_warning: str | None) -> dict[str, bool | str]:
    result: dict[str, bool | str] = {"restarted": restarted}
    if waf_warning:
        result["waf_warning"] = waf_warning
    return result


def apply_post_save_changes(
    bench_root: Path,
    config: BenchConfig,
    old_restart: dict,
    old_firewall: dict,
    old_waf: dict,
    old_s3_config: dict,
) -> tuple[bool, str | None]:
    return Bench(config, bench_root).apply_saved_settings(
        old_restart,
        old_firewall,
        old_waf,
        old_s3_config,
    )
