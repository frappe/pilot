from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config import BenchTomlStore

if TYPE_CHECKING:
    from pilot.managers.database import MariaDBManager, PostgresManager


def database_validation(bench_root: Path, data: dict):
    engine = data.get("engine")
    if engine not in ("mariadb", "postgres"):
        raise ValueError("engine must be 'mariadb' or 'postgres'.")

    for field in ("password", "admin_user", "host"):
        if field in data and not isinstance(data[field], str):
            raise ValueError(f"{field} must be a string.")
    if "existing" in data and not isinstance(data["existing"], bool):
        raise ValueError("existing must be a boolean.")

    default_port = 3306 if engine == "mariadb" else 5432
    port = data.get("port", default_port)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535.")

    password = data.get("password", "")
    default_admin_user = "root" if engine == "mariadb" else "postgres"
    admin_user = (data.get("admin_user") or default_admin_user).strip()
    host = (data.get("host") or "localhost").strip()
    existing = data.get("existing", False)
    if existing and (not host or not admin_user):
        raise ValueError("host and admin_user are required for an existing server.")

    if engine == "mariadb":
        from pilot.managers.database import MariaDBManager

        config = _mariadb_config(
            bench_root,
            password,
            admin_user,
            host,
            port,
            existing,
        )
        return engine, MariaDBManager(config), password, existing

    from pilot.config import PostgresConfig
    from pilot.managers.database import PostgresManager

    config = PostgresConfig(
        host=host,
        port=port,
        root_password=password,
        admin_user=admin_user,
        existing=existing,
    )
    return engine, PostgresManager(config), password, existing


def database_validation_state(manager, password: str, existing: bool) -> str:
    if existing:
        return "valid" if manager.check_credentials(password) else "invalid"
    if _is_fresh_install(manager):
        return "will_install"
    return "valid" if manager.check_credentials(password) else "invalid"


def _is_fresh_install(manager: PostgresManager | MariaDBManager) -> bool:
    """Return whether init will provision and secure the DB server."""
    if not manager.is_installed():
        return True
    return not manager.is_provisioned()


def _mariadb_config(
    bench_root: Path,
    password: str,
    admin_user: str = "root",
    host: str = "",
    port=None,
    existing: bool = False,
):
    from pilot.config import MariaDBConfig

    config = MariaDBConfig(
        root_password=password,
        admin_user=admin_user,
        host=host or "localhost",
        port=int(port or 3306),
        existing=existing,
    )
    toml_path = bench_root / "bench.toml"
    if toml_path.exists():
        try:
            settings = BenchTomlStore(toml_path).read_flat()
            config.socket_path = settings.get("mariadb_socket_path", "") or ""
        except Exception as exc:
            logging.debug("Could not read the existing mariadb socket path: %s", exc)
    return config
