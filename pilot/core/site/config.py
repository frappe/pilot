from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from pathlib import Path

from pilot.core.database import Database, make_site_database
from pilot.exceptions import BenchError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked

_PROBE_CONNECT_TIMEOUT = 5
_TRUE_SINGLES_VALUES = frozenset({"1", "true"})

PROTECTED_CONFIG_KEYS = frozenset(
    {
        "backup_retention",
        # Pilot pins it on a rename; a site-scoped caller setting it could squat a
        # certificate lineage another site or a future one would need.
        "cert_name",
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
        "route",
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


def list_installed_apps(site_config: dict, bench_root: Path, site_name: str) -> list[str]:
    """Every app installed on the site, the disabled ones included."""
    if isinstance(site_config.get("installed_apps"), list):
        return site_config["installed_apps"]
    apps = query_installed_apps_via_db(bench_root, site_name)
    if apps is not None:
        return apps
    return query_installed_apps_via_frappe(bench_root, site_name)


def list_active_apps(site_config: dict, bench_root: Path, site_name: str) -> list[str]:
    """Installed apps the site actually runs - what the Admin UI shows as installed."""
    installed = list_installed_apps(site_config, bench_root, site_name)
    return exclude_disabled_apps(installed, bench_root, site_name)


def exclude_disabled_apps(apps: list[str], bench_root: Path, site_name: str) -> list[str]:
    """Drop the apps the site holds disabled - to Pilot those are not installed."""
    disabled = set(query_disabled_apps_via_db(bench_root, site_name))
    return [app for app in apps if app not in disabled]


def query_disabled_apps_via_db(bench_root: Path, site_name: str) -> list[str]:
    """Read the site's disabled apps from Frappe's global setting."""
    rows = _query_site_database(
        bench_root,
        site_name,
        lambda database: (
            f"SELECT defvalue FROM {database.quote_identifier('tabDefaultValue')} "
            "WHERE parent = '__global' AND defkey = 'disabled_apps'"
        ),
    )
    if not rows:
        return []
    try:
        disabled = json.loads(str(rows[0][0]) or "[]")
    except json.JSONDecodeError:
        return []
    return [app for app in disabled if isinstance(app, str)] if isinstance(disabled, list) else []


def has_app_disabling(bench_root: Path, site_name: str) -> bool:
    """Whether Frappe knows the Installed Application `disabled` field. No row means a
    version that predates app disabling."""
    rows = _query_site_database(
        bench_root,
        site_name,
        lambda database: (
            f"SELECT fieldname FROM {database.quote_identifier('tabDocField')} "
            "WHERE parent = 'Installed Application' AND fieldname = 'disabled'"
        ),
    )
    return bool(rows)


def query_installed_apps_via_db(bench_root: Path, site_name: str) -> list[str] | None:
    """None means the site database was unreachable, not that it has no apps."""
    rows = _query_site_database(
        bench_root,
        site_name,
        lambda database: (
            f"SELECT app_name FROM {database.quote_identifier('tabInstalled Application')} ORDER BY idx"
        ),
    )
    if rows is None:
        return None
    return [str(row[0]).strip() for row in rows if row and str(row[0]).strip()]


def is_setup_complete(bench_root: Path, site_name: str) -> bool | None:
    """Whether every active setup-wizard app has finished configuration."""
    apps = query_setup_wizard_apps_via_db(bench_root, site_name)
    if not apps:
        return _query_site_setup_flag(bench_root, site_name)
    disabled = set(query_disabled_apps_via_db(bench_root, site_name))
    return all(finished for app, finished in apps if app not in disabled)


def query_setup_wizard_apps_via_db(bench_root: Path, site_name: str) -> list[tuple[str, bool]] | None:
    """(app, finished) for each app that ships a setup wizard. None on a Frappe that does
    not track setup per app, where the columns are missing and the query fails."""
    rows = _query_site_database(
        bench_root,
        site_name,
        lambda database: (
            "SELECT app_name, is_setup_complete "
            f"FROM {database.quote_identifier('tabInstalled Application')} WHERE has_setup_wizard = 1"
        ),
    )
    if rows is None:
        return None
    return [
        (str(row[0]).strip(), str(row[1]).strip().lower() in _TRUE_SINGLES_VALUES)
        for row in rows
        if row and str(row[0]).strip()
    ]


def _query_site_setup_flag(bench_root: Path, site_name: str) -> bool | None:
    """The site-wide System Settings flag, for a Frappe that tracks no more than that."""
    rows = _query_site_database(
        bench_root,
        site_name,
        lambda database: (
            f"SELECT value FROM {database.quote_identifier('tabSingles')} "
            "WHERE doctype = 'System Settings' AND field = 'setup_complete'"
        ),
    )
    if rows is None:
        return None
    if not rows:
        return False
    return str(rows[0][0]).strip().lower() in _TRUE_SINGLES_VALUES


def _query_site_database(
    bench_root: Path,
    site_name: str,
    build_query: Callable[[Database], str],
) -> list[list] | None:
    """Read-only probe against a site's own database, whichever engine backs it.
    `build_query` receives the engine so identifiers get quoted its way."""
    try:
        database = make_site_database(bench_root, site_name, connect_timeout=_PROBE_CONNECT_TIMEOUT)
        return database.execute(build_query(database)).rows
    except Exception:
        return None


def set_site_ssl_flag(sites_root: Path, site_name: str, enabled: bool) -> None:
    config_path = safe_site_config_path(sites_root, site_name)
    with exclusive_file_lock(config_path):
        config = json.loads(config_path.read_text())
        config["ssl"] = enabled
        replace_private_text_locked(config_path, json.dumps(config, indent=1))


def clear_certificate_pin(sites_root: Path, site_name: str) -> None:
    """Drop a site's pinned certificate lineage, under the config lock."""
    config_path = safe_site_config_path(sites_root, site_name)
    with exclusive_file_lock(config_path):
        config = json.loads(config_path.read_text())
        if config.pop("cert_name", None) is None:
            return
        replace_private_text_locked(config_path, json.dumps(config, indent=1))


def read_public_config(site_path: Path) -> dict:
    config = read_site_config(site_path)
    return public_config(config)


def update_public_config(site_path: Path, patch: dict) -> dict:
    config_path = site_path / "site_config.json"
    with exclusive_file_lock(config_path):
        current = json.loads(config_path.read_text())
        if not isinstance(current, dict):
            raise ValueError("Site configuration must be a JSON object.")
        if error := config_patch_error(current, patch):
            raise BenchError(error)
        merged = merge_public_config(current, patch)
        replace_private_text_locked(config_path, json.dumps(merged, indent=1))
    return public_config(merged)


def read_site_config(site_path: Path) -> dict:
    config = json.loads((site_path / "site_config.json").read_text())
    if not isinstance(config, dict):
        raise ValueError("Site configuration must be a JSON object.")
    return config


def public_config(config: dict) -> dict:
    return {key: _public_config_value(value) for key, value in config.items() if is_public_config_key(key)}


def merge_public_config(current: dict, submitted: dict) -> dict:
    merged = copy.deepcopy(current)
    for key, submitted_value in submitted.items():
        if submitted_value is None:
            merged.pop(key, None)
            continue
        current_value = current.get(key)
        merged[key] = _merge_public_value(current_value, submitted_value)
    return merged


def config_patch_error(current, submitted) -> str | None:
    if not isinstance(submitted, dict):
        return "Configuration patches must be JSON objects."
    for key, value in submitted.items():
        error = _config_patch_item_error(current, key, value)
        if error:
            return error
    return None


def _config_patch_item_error(current, key, value) -> str | None:
    if not isinstance(key, str) or not is_public_config_key(key):
        return "System-managed and secret-like configuration keys cannot be changed."

    existing = current.get(key) if isinstance(current, dict) else None
    if value is None:
        return _config_remove_error(existing)
    if isinstance(value, dict):
        nested = existing if isinstance(existing, dict) else {}
        return config_patch_error(nested, value)
    if isinstance(value, list):
        return _config_list_replace_error(existing, value)
    if _has_protected_config(existing):
        return "A configuration value containing protected fields cannot change type."
    return None


def _config_remove_error(existing) -> str | None:
    if _has_protected_config(existing):
        return "A configuration value containing protected fields cannot be removed."
    return None


def _config_list_replace_error(existing, value: list) -> str | None:
    if _has_protected_config(existing):
        return "A list containing protected fields cannot be replaced."
    for item in value:
        error = _submitted_config_value_error(item)
        if error:
            return error
    return None


def is_public_config_key(key: str) -> bool:
    normalized = (
        re.sub(
            r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])",
            "_",
            key,
        )
        .lower()
        .replace("-", "_")
    )
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


def safe_site_config_path(sites_root: Path, site_name: str) -> Path:
    if sites_root.is_symlink():
        raise BenchError("Site configuration path must stay within the bench.")
    resolved_root = sites_root.resolve()
    site_path = resolved_root / site_name
    config_path = site_path / "site_config.json"
    if (
        site_path.is_symlink()
        or site_path.resolve(strict=False).parent != resolved_root
        or config_path.is_symlink()
        or not config_path.is_file()
    ):
        raise BenchError("Site configuration is unavailable.")
    return config_path


def _public_config_value(value):
    if isinstance(value, dict):
        return public_config(value)
    if isinstance(value, list):
        return [_public_config_value(item) for item in value]
    return copy.deepcopy(value)


def _merge_public_value(current, submitted):
    if isinstance(current, dict) and isinstance(submitted, dict):
        return merge_public_config(current, submitted)
    return copy.deepcopy(submitted)


def _submitted_config_value_error(value) -> str | None:
    if isinstance(value, dict):
        return config_patch_error({}, value)
    if isinstance(value, list):
        for item in value:
            if error := _submitted_config_value_error(item):
                return error
    return None


def _has_protected_config(value) -> bool:
    if isinstance(value, dict):
        return any(
            not is_public_config_key(key) or _has_protected_config(child) for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_has_protected_config(child) for child in value)
    return False


def query_installed_apps_via_frappe(bench_root: Path, site_name: str) -> list[str]:
    import os
    import subprocess

    python = str(bench_root / "env" / "bin" / "python")
    sites_dir = str(bench_root / "sites")
    try:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [
                python,
                "-m",
                "frappe.utils.bench_helper",
                "frappe",
                "--site",
                site_name,
                "list-apps",
            ],
            cwd=sites_dir,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        if result.returncode != 0:
            return []
        return [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return []
