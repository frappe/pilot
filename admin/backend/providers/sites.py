from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pilot.core.site import exclude_disabled_apps, is_setup_complete, query_installed_apps_via_db
from pilot.internal.site_paths import resolve_site_path
from pilot.managers.task import TaskReader

# These write site_config.json well before the DB is queryable, so a failed
# DB probe during one means "not ready yet", not "broken".
_PROVISIONING_COMMANDS = {"new-site", "new-site-from-backup", "reinstall-site", "rename-site"}
_PROVISIONING_ARG_KEYS = ("name", "site", "new_name")


@dataclass
class SiteInfo:
    name: str
    exists: bool
    db_name: str
    db_host: str
    db_type: str
    active_apps: list[str]
    site_config: dict
    broken: bool = False
    provisioning: bool = False
    setup_complete: bool = False


class SiteProvider:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def get_all(self) -> list[SiteInfo]:
        sites_path = self._bench_root / "sites"
        if sites_path.is_symlink() or not sites_path.is_dir():
            return []

        provisioning = self.provisioning_site_names
        return [
            self.get_site(d.name, provisioning)
            for d in sorted(sites_path.iterdir())
            if not d.is_symlink()
            and d.is_dir()
            and not (d / "site_config.json").is_symlink()
            and (d / "site_config.json").is_file()
        ]

    def get_one(self, site_name: str) -> SiteInfo:
        return self.get_site(site_name, self.provisioning_site_names)

    @property
    def provisioning_site_names(self) -> set[str]:
        """Sites with an active provisioning task - cheap local file reads,
        versus the DB probe they let us skip."""
        try:
            tasks = TaskReader(self._bench_root).list_tasks()
        except Exception:
            return set()

        names = set()
        for task in tasks:
            if not task.status.is_active or task.command not in _PROVISIONING_COMMANDS:
                continue
            for key in _PROVISIONING_ARG_KEYS:
                if name := task.args.get(key):
                    names.add(name)
        return names

    def get_site(self, site_name: str, provisioning: set[str]) -> SiteInfo:
        site_path = resolve_site_path(self._bench_root, site_name)
        if site_path is None:
            raise ValueError("Site path must stay within the bench.")

        site_config_path = site_path / "site_config.json"
        exists = not site_config_path.is_symlink() and site_config_path.is_file()
        site_config: dict = {}

        if exists:
            try:
                site_config = json.loads(site_config_path.read_text())
            except (json.JSONDecodeError, OSError):
                site_config = {}

        is_provisioning = site_name in provisioning
        active: list = []
        broken = False
        setup_complete = False

        if exists:
            active, broken = self._read_active_apps(site_config, site_name, is_provisioning)
            if not is_provisioning and not broken:
                setup_complete = bool(is_setup_complete(self._bench_root, site_name))

        return SiteInfo(
            name=site_name,
            exists=exists,
            db_name=site_config.get("db_name", ""),
            db_host=site_config.get("db_host") or "localhost",
            db_type=site_config.get("db_type") or "mariadb",
            active_apps=active,
            site_config=site_config,
            broken=broken,
            provisioning=is_provisioning,
            setup_complete=setup_complete,
        )

    def _read_active_apps(
        self, site_config: dict, site_name: str, is_provisioning: bool
    ) -> tuple[list, bool]:
        """Apps in use on the site, and whether it looks broken. A provisioning site is
        never probed - its database is not up yet."""
        apps = site_config.get("installed_apps")
        if is_provisioning:
            return apps if isinstance(apps, list) else [], False
        if not isinstance(apps, list):
            apps = query_installed_apps_via_db(self._bench_root, site_name)
            if apps is None:
                return [], True
        return exclude_disabled_apps(apps, self._bench_root, site_name), False
