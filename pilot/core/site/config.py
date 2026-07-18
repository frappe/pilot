from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from pilot.exceptions import BenchError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked

_DB_SOCKET_CANDIDATES = [
    "/var/run/mysqld/mysqld.sock",
    "/run/mysqld/mysqld.sock",
    "/tmp/mysql.sock",
    "/usr/local/var/mysql/mysql.sock",
]

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


def list_installed_apps(site_config: dict, bench_root: Path, site_name: str) -> list[str]:
    if isinstance(site_config.get("installed_apps"), list):
        return site_config["installed_apps"]
    apps = query_installed_apps_via_db(site_config)
    if apps is not None:
        return apps
    return query_installed_apps_via_frappe(bench_root, site_name)


def query_installed_apps_via_db(site_config: dict) -> list[str] | None:
    import shutil
    import subprocess

    db_name = site_config.get("db_name", "")
    db_password = site_config.get("db_password", "")
    db_host = site_config.get("db_host") or "localhost"
    db_port = int(site_config.get("db_port") or 3306)
    if not db_name or not db_password:
        return None

    cli = shutil.which("mariadb") or shutil.which("mysql")
    if not cli:
        return None

    conn_args = [f"--user={db_name}", f"--password={db_password}"]
    if db_host in ("localhost", "127.0.0.1", ""):
        socket_path = next((socket for socket in _DB_SOCKET_CANDIDATES if Path(socket).exists()), None)
        if socket_path:
            conn_args.append(f"--socket={socket_path}")
        else:
            conn_args += ["--host=127.0.0.1", f"--port={db_port}"]
    else:
        conn_args += [f"--host={db_host}", f"--port={db_port}"]

    try:
        result = subprocess.run(
            [
                cli,
                *conn_args,
                "--batch",
                "--skip-column-names",
                db_name,
                "-e",
                "SELECT app_name FROM `tabInstalled Application` ORDER BY idx",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return None


def set_site_ssl_flag(sites_root: Path, site_name: str, enabled: bool) -> None:
    config_path = safe_site_config_path(sites_root, site_name)
    with exclusive_file_lock(config_path):
        config = json.loads(config_path.read_text())
        config["ssl"] = enabled
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
