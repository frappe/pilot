from __future__ import annotations

import json
import secrets
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.utils import write_private_text

if TYPE_CHECKING:
    from pilot.core.worktree.site_database import SiteDatabase

_DROPPED_KEYS = ("pilot_auth_token", "pilot_endpoint", "host_name", "domains", "ssl", "route", "cert_name")
_SITE_DIRECTORIES = ("db", "public", "private", "locks", "logs")


class SiteClone:
    """Copy a site under a new name, with its own database `db_name` and developer mode on."""

    def __init__(self, source: Path, target: Path, database: "SiteDatabase", db_name: str) -> None:
        self.source = source
        self.target = target
        self.database = database
        self.db_name = db_name

    def run(self) -> None:
        """The config is written before the database is made, so undoing a failed add finds it."""
        if self.target.exists():
            raise BenchError(f"Site directory {self.target} already exists.")
        source_config = json.loads((self.source / "site_config.json").read_text())
        config = self.get_clone_config(source_config, self.db_name)
        for name in _SITE_DIRECTORIES:
            (self.target / name).mkdir(parents=True, exist_ok=True)
        write_private_text(self.target / "site_config.json", json.dumps(config, indent=1))
        self.database.copy(self.source, self.target, source_config, config)
        self.copy_files()

    @staticmethod
    def get_clone_config(source_config: dict, db_name: str) -> dict:
        """Source config with a new database, developer mode on, and public identity and Admin
        plane settings dropped: the clone answers only on its own name, never for the base site.
        encryption_key is kept so encrypted fields still decrypt. Mail and scheduled jobs are
        off, since the cloned data holds the base site's live credentials. A server database
        gets its own account: named after the database, with a new password."""
        config = {key: value for key, value in source_config.items() if key not in _DROPPED_KEYS}
        config.pop("db_user", None)
        config["db_name"] = db_name
        if "db_password" in source_config:
            config["db_password"] = secrets.token_hex(16)
        config["developer_mode"] = 1
        config["mute_emails"] = 1
        config["pause_scheduler"] = 1
        return config

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
