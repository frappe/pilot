from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from pilot.core.database import Database, make_site_database


class SiteDatabaseState:
    """Facts read from a site's own database, whichever engine backs it.

    Each read is a short read-only probe. A probe that cannot reach the database returns
    None, or the safe default that the property names, so callers can fall back."""

    connect_timeout = 5
    true_values = frozenset({"1", "true"})

    def __init__(self, bench_root: Path, site_name: str) -> None:
        self.bench_root = bench_root
        self.site_name = site_name

    @property
    def installed_apps(self) -> list[str] | None:
        """Rows of Installed Application, in install order. None when unreachable."""
        rows = self.query(
            lambda database: (
                f"SELECT app_name FROM {database.quote_identifier('tabInstalled Application')} ORDER BY idx"
            )
        )
        if rows is None:
            return None
        return [str(row[0]).strip() for row in rows if row and str(row[0]).strip()]

    @property
    def committed_apps(self) -> list[str] | None:
        """The installed apps Frappe committed in its global setting. None when unreadable."""
        return self.get_global_app_list("installed_apps")

    @property
    def disabled_apps(self) -> list[str]:
        """Apps the site keeps installed but turned off."""
        return self.get_global_app_list("disabled_apps") or []

    def exclude_disabled(self, apps: list[str]) -> list[str]:
        """Drop the apps the site holds disabled - to Pilot those are not installed."""
        disabled = set(self.disabled_apps)
        return [app for app in apps if app not in disabled]

    @property
    def has_app_disabling(self) -> bool:
        """Whether this Frappe can disable an app. Older versions have no `disabled` field."""
        rows = self.query(
            lambda database: (
                f"SELECT fieldname FROM {database.quote_identifier('tabDocField')} "
                "WHERE parent = 'Installed Application' AND fieldname = 'disabled'"
            )
        )
        return bool(rows)

    def has_module_rows(self, app_name: str) -> bool:
        """Whether an earlier install attempt already wrote this app's module rows."""
        if not re.fullmatch(r"[a-z][a-z0-9_]*", app_name):
            return False
        rows = self.query(
            lambda database: (
                f"SELECT 1 FROM {database.quote_identifier('tabModule Def')} WHERE app_name = '{app_name}' LIMIT 1"
            )
        )
        return bool(rows)

    @property
    def is_setup_complete(self) -> bool | None:
        """Whether every active setup-wizard app has finished configuration."""
        apps = self.setup_wizard_apps
        if not apps:
            return self.is_system_setting_true("setup_complete")
        disabled = set(self.disabled_apps)
        return all(finished for app, finished in apps if app not in disabled)

    @property
    def setup_wizard_apps(self) -> list[tuple[str, bool]] | None:
        """(app, finished) for each app that ships a setup wizard. None on a Frappe that does
        not track setup per app, where the columns are missing and the query fails."""
        rows = self.query(
            lambda database: (
                "SELECT app_name, is_setup_complete "
                f"FROM {database.quote_identifier('tabInstalled Application')} WHERE has_setup_wizard = 1"
            )
        )
        if rows is None:
            return None
        return [
            (str(row[0]).strip(), str(row[1]).strip().lower() in self.true_values)
            for row in rows
            if row and str(row[0]).strip()
        ]

    @property
    def is_backup_encryption_enabled(self) -> bool:
        """True when the probe fails too: streaming an encrypted site would upload plaintext."""
        enabled = self.is_system_setting_true("encrypt_backup")
        return enabled is None or enabled

    def is_system_setting_true(self, field: str) -> bool | None:
        """Whether a System Settings checkbox is on. None when the database is unreachable."""
        rows = self.query(
            lambda database: (
                f"SELECT value FROM {database.quote_identifier('tabSingles')} "
                f"WHERE doctype = 'System Settings' AND field = '{field}'"
            )
        )
        if rows is None:
            return None
        return bool(rows) and str(rows[0][0]).strip().lower() in self.true_values

    def get_global_app_list(self, key: str) -> list[str] | None:
        """An app list Frappe keeps in its global defaults. None when missing or unreadable."""
        rows = self.query(
            lambda database: (
                f"SELECT defvalue FROM {database.quote_identifier('tabDefaultValue')} "
                f"WHERE parent = '__global' AND defkey = '{key}'"
            )
        )
        if not rows:
            return None
        try:
            apps = json.loads(str(rows[0][0]) or "[]")
        except json.JSONDecodeError:
            return None
        return [app for app in apps if isinstance(app, str)] if isinstance(apps, list) else None

    def query(self, build_query: Callable[[Database], str]) -> list[list] | None:
        """Run one read-only query; None when the database cannot be reached.
        `build_query` receives the engine so identifiers get quoted its way."""
        try:
            database = make_site_database(
                self.bench_root, self.site_name, connect_timeout=self.connect_timeout
            )
            return database.execute(build_query(database)).rows
        except Exception:
            return None
