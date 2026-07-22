from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.core.database.base import BinlogFile, BinlogStatus, Database, LockWaitStatus, QueryResult
from pilot.core.database.engines import MariaDB, PostgreSQL, SQLite
from pilot.exceptions import DatabaseError

if TYPE_CHECKING:
    from pilot.config import BenchConfig

__all__ = [
    "BinlogFile",
    "BinlogStatus",
    "Database",
    "LockWaitStatus",
    "MariaDB",
    "PostgreSQL",
    "QueryResult",
    "SQLite",
    "make_database",
    "make_site_database",
]


def make_database(config: "BenchConfig") -> Database:
    """Root-level admin database connection for a bench (not site-specific)."""
    if config.db_type == "sqlite":
        raise DatabaseError("SQLite has no shared server; use make_site_database() for site access")
    if config.db_type == "postgres":
        postgres = config.postgres
        return PostgreSQL(
            host=postgres.host,
            port=postgres.port,
            user=postgres.admin_user,
            password=postgres.root_password or "trust_auth",
            database=postgres.admin_user,
        )
    mariadb = config.mariadb
    return MariaDB(
        host=mariadb.host,
        port=mariadb.port,
        user=mariadb.admin_user,
        password=mariadb.root_password,
        database="",
        socket=mariadb.socket_path or None,
    )


def make_site_database(bench_root: Path | str, site_name: str) -> Database:
    """Site-specific database connection from site_config.json."""
    # site_name is attacker-controlled. Reject anything that is not a single
    # path segment so it cannot escape sites/<site>/site_config.json.
    if not site_name or "/" in site_name or "\\" in site_name or site_name in (".", ".."):
        raise FileNotFoundError(f"Site '{site_name}' not found")
    config_path = Path(bench_root) / "sites" / site_name / "site_config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Site '{site_name}' not found")
    config = json.loads(config_path.read_text())
    db_type = config.get("db_type", "mariadb")
    if db_type == "postgres":
        return PostgreSQL(
            host=config.get("db_host", "localhost"),
            port=int(config.get("db_port", 5432)),
            user=config["db_user"],
            password=config["db_password"],
            database=config["db_name"],
        )
    if db_type == "sqlite":
        # Frappe stores SQLite under sites/<site>/db/, not directly in the site folder.
        db_file = Path(bench_root) / "sites" / site_name / "db" / f"{config.get('db_name', site_name)}.db"
        return SQLite(db_path=str(db_file))
    return MariaDB(
        host=config.get("db_host", "localhost"),
        port=int(config.get("db_port", 3306)),
        user=config["db_user"],
        password=config["db_password"],
        database=config["db_name"],
        socket=config.get("db_socket") or None,
    )
