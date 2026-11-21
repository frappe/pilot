from __future__ import annotations

import typing
from dataclasses import dataclass

if typing.TYPE_CHECKING:
    from pilot.core.site import Site


@dataclass
class AppInstall:
    app: str
    installed_at: str


@dataclass
class SiteHistory:
    created_at: str
    updated_at: str
    frappe_version: str
    frappe_branch: str
    apps: list[AppInstall]
    users: int = 0


class SiteHistoryReader:
    """When the site was created, last migrated and each app installed.
    Frappe rewrites Installed Application rows on every migrate, so their time is the last update.
    Install times come from Pilot's audit log, falling back to the site's creation."""

    def __init__(self, site: "Site") -> None:
        self.site = site

    def get(self) -> SiteHistory:
        """The site history from its database and the bench audit log."""
        from pilot.core.database import make_site_database

        database = make_site_database(self.site.bench.path, self.site.config.name)
        user = database.quote_identifier("tabUser")
        installed = database.quote_identifier("tabInstalled Application")
        created = database.execute(f"SELECT creation FROM {user} WHERE name = 'Administrator'").rows
        rows = database.execute(f"SELECT app_name, app_version, git_branch, modified FROM {installed} ORDER BY idx").rows
        users = database.execute(
            f"SELECT COUNT(*) FROM {user} WHERE enabled = 1 AND user_type = 'System User'"
            " AND name NOT IN ('Administrator', 'Guest')"
        ).rows
        created_at = str(created[0][0]) if created else ""
        history = build_history(created_at, rows, self.get_install_times())
        history.users = int(users[0][0]) if users else 0

        return history

    def get_install_times(self) -> dict[str, str]:
        """The latest install time Pilot logged for each app on this site."""
        from pilot.core.bench.audit_log import AuditLog

        times: dict[str, str] = {}
        for entry in AuditLog(self.site.bench).entries(entry_type="app", site=self.site.config.name):
            if entry.get("event") == "installed":
                times.setdefault(entry["app"], entry["logged_at"])
        return times


def build_history(created_at: str, rows: list[list], install_times: dict[str, str]) -> SiteHistory:
    """The history from Installed Application rows, frappe first."""
    frappe = next((row for row in rows if row[0] == "frappe"), None)
    apps = [AppInstall(app=row[0], installed_at=install_times.get(row[0], created_at)) for row in rows]

    return SiteHistory(
        created_at=created_at,
        updated_at=str(max((row[3] for row in rows), default="")),
        frappe_version=frappe[1] if frappe else "",
        frappe_branch=frappe[2] if frappe else "",
        apps=apps,
    )
