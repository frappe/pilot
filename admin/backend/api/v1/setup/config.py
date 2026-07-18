from __future__ import annotations

import logging
from pathlib import Path

from admin.backend.api.v1.setup.state import setup_handoff_task
from pilot.config import BenchConfig
from pilot.internal.validators import validate_branch_name, validate_repo_url

_PASSWORD_KEYS = ("admin_password", "mariadb_password", "postgres_password")


def validate_configuration(data: dict) -> str | None:
    if error := _validate_field_types(data):
        return error
    if error := _validate_required_fields(data):
        return error
    if error := _validate_existing_database(data):
        return error
    return _validate_app_source(data)


def _validate_field_types(data: dict) -> str | None:
    text_fields = (
        "admin_password",
        "app_branch",
        "app_repo",
        "db_type",
        "mariadb_admin_user",
        "mariadb_host",
        "mariadb_password",
        "postgres_admin_user",
        "postgres_host",
        "postgres_password",
    )
    for field in text_fields:
        if field in data and not isinstance(data[field], str):
            return f"{field} must be a string"
    for field in ("mariadb_existing", "postgres_existing"):
        if field in data and not isinstance(data[field], bool):
            return f"{field} must be a boolean"
    for field in ("mariadb_port", "postgres_port"):
        value = data.get(field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535
        ):
            return f"{field} must be an integer between 1 and 65535"
    return None


def _validate_required_fields(data: dict) -> str | None:
    if not data.get("admin_password"):
        return "admin_password is required"
    db_type = data.get("db_type", "mariadb")
    if db_type not in ("mariadb", "postgres"):
        return "db_type must be 'mariadb' or 'postgres'"
    if db_type == "mariadb" and not data.get("mariadb_password"):
        return "mariadb_password is required"
    if db_type == "postgres" and not data.get("postgres_password"):
        return "postgres_password is required"
    return None


def _validate_existing_database(data: dict) -> str | None:
    db_type = data.get("db_type", "mariadb")
    if data.get(f"{db_type}_existing"):
        if not data.get(f"{db_type}_host"):
            return f"{db_type}_host is required when connecting to an existing database server"
        if not data.get(f"{db_type}_admin_user"):
            return f"{db_type}_admin_user is required when connecting to an existing database server"
    return None


def _validate_app_source(data: dict) -> str | None:
    if "app_repo" in data and (error := validate_repo_url(data["app_repo"])):
        return error
    if "app_branch" in data and (error := validate_branch_name(data["app_branch"])):
        return error
    return None


def read_defaults(bench_root: Path) -> dict:
    from pilot.managers.platform import is_linux, native_process_manager

    # This is a read endpoint the wizard polls before login - it must never echo
    # a DB password back, default or real, whether or not bench.toml has one set.
    defaults = {key: value for key, value in BenchConfig.default_flat_settings().items() if key not in _PASSWORD_KEYS}

    result = {
        "bench_name": bench_root.name,
        "is_linux": is_linux(),
        "native_process_manager": native_process_manager(),
        **defaults,
    }
    toml_path = bench_root / "bench.toml"
    if toml_path.exists():
        try:
            settings = BenchConfig.read_flat(toml_path)
            for key in _PASSWORD_KEYS:
                result[f"{key}_configured"] = bool(settings.get(key))
                settings.pop(key, None)
            result.update(settings)
            if not result.get("bench_name"):
                result["bench_name"] = bench_root.name
        except Exception as exc:
            logging.debug("Could not read bench.toml settings: %s", exc)

    for key in _PASSWORD_KEYS:
        result.setdefault(f"{key}_configured", False)

    try:
        task = setup_handoff_task(bench_root)
        result["running_setup_task_id"] = task.task_id if task else None
    except Exception:
        result["running_setup_task_id"] = None

    return result
