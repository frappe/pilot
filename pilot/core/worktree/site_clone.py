from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from pilot.exceptions import BenchError
from pilot.utils import write_private_text

# Settings that tie a site to its public identity or to the Admin plane. The clone
# answers only on its own name and must not act for the base site.
_DROPPED_KEYS = ("pilot_auth_token", "pilot_endpoint", "host_name", "domains", "ssl", "route", "cert_name")
_SITE_DIRECTORIES = ("db", "public", "private", "locks", "logs")


class SiteClone:
    """Copy a SQLite site under a new name, with its own database and developer mode on."""

    def __init__(self, source: Path, target: Path, db_type: str) -> None:
        self.source = source
        self.target = target
        self.db_type = db_type

    def check_supported(self) -> None:
        if self.db_type != "sqlite":
            raise BenchError(f"Worktrees need a SQLite bench; {self.db_type} is not supported yet.")

    def run(self) -> None:
        self.check_supported()
        if self.target.exists():
            raise BenchError(f"Site directory {self.target} already exists.")
        source_config = json.loads((self.source / "site_config.json").read_text())
        config = self.get_clone_config(source_config)
        for name in _SITE_DIRECTORIES:
            (self.target / name).mkdir(parents=True, exist_ok=True)
        write_private_text(self.target / "site_config.json", json.dumps(config, indent=1))
        self.copy_database(
            self.source / "db" / f"{source_config['db_name']}.db",
            self.target / "db" / f"{config['db_name']}.db",
        )
        self.copy_files()

    @staticmethod
    def get_clone_config(source_config: dict) -> dict:
        """Source config with a new database name, developer mode on and public identity dropped.
        encryption_key is kept so encrypted fields still decrypt. Mail and scheduled jobs are
        off, since the cloned data holds the base site's live credentials."""
        config = {key: value for key, value in source_config.items() if key not in _DROPPED_KEYS}
        config["db_name"] = f"_{secrets.token_hex(8)}"
        config["developer_mode"] = 1
        config["mute_emails"] = 1
        config["pause_scheduler"] = 1
        return config

    @staticmethod
    def copy_database(source: Path, target: Path) -> None:
        """Copy through SQLite's backup API, which includes writes still in the WAL."""
        if not source.exists():
            raise BenchError(f"Database {source} not found.")
        with closing(sqlite3.connect(source)) as source_db, closing(sqlite3.connect(target)) as target_db:
            source_db.backup(target_db)

    def copy_files(self) -> None:
        """Public and private files, without the base site's backups."""
        private = self.source / "private"
        for name in ("public", "private"):
            if (self.source / name).is_dir():
                shutil.copytree(
                    self.source / name,
                    self.target / name,
                    dirs_exist_ok=True,
                    ignore=lambda directory, _names: ["backups"] if Path(directory) == private else [],
                )
