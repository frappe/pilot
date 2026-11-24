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
    An app's Module Def rows are written once at install, so their oldest time is the install time."""

    def __init__(self, site: "Site") -> None:
        self.site = site

    def get(self) -> SiteHistory:
        """The site history from its database."""
        from pilot.core.database import make_site_database

        database = make_site_database(self.site.bench.path, self.site.config.name)
        user = database.quote_identifier("tabUser")
        installed = database.quote_identifier("tabInstalled Application")
        mark = database.placeholder
        created = database.execute(
            f"SELECT creation FROM {user} WHERE name = {mark}", params=["Administrator"]
        ).rows
        rows = database.execute(
            f"SELECT app_name, app_version, git_branch, modified FROM {installed} ORDER BY idx"
        ).rows
        modules = database.quote_identifier("tabModule Def")
        install_times = database.execute(
            f"SELECT app_name, MIN(creation) FROM {modules} GROUP BY app_name"
        ).rows
        users = database.execute(
            f"SELECT COUNT(*) FROM {user} WHERE enabled = 1 AND user_type = {mark} AND name NOT IN ({mark}, {mark})",
            params=["System User", "Administrator", "Guest"],
        ).rows
        created_at = str(created[0][0]) if created else ""
        history = build_history(created_at, rows, {str(app): str(time) for app, time in install_times})
        history.users = int(users[0][0]) if users else 0

        return history


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
