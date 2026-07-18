from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pilot.managers.task import TaskReader
from pilot.core.site import query_installed_apps_via_db
from pilot.internal.site_paths import resolve_site_path

# These write site_config.json well before the DB is queryable, so a failed
# DB probe during one means "not ready yet", not "broken".
_PROVISIONING_COMMANDS = {"new-site", "new-site-from-backup", "reinstall-site"}
_PROVISIONING_ARG_KEYS = ("name", "site")


@dataclass
class SiteInfo:
    name: str
    exists: bool
    db_name: str
    db_host: str
    db_type: str
    installed_apps: list[str]
    site_config: dict
    broken: bool = False
    provisioning: bool = False


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
        """Sites with an active provisioning task — cheap local file reads,
        versus the DB probe they let us skip."""
        try:
            tasks = TaskReader(self._bench_root).list_tasks()
        except Exception:
            return set()

        names = set()
        for task in tasks:
            if (
                not task.status.is_active
                or task.command not in _PROVISIONING_COMMANDS
            ):
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
        installed_apps: list[str] = []
        broken = False

        if exists:
            if isinstance(site_config.get("installed_apps"), list):
                installed_apps = site_config["installed_apps"]
            elif not is_provisioning:
                apps = query_installed_apps_via_db(site_config)
                if apps is not None:
                    installed_apps = apps
                else:
                    broken = True

        return SiteInfo(
            name=site_name,
            exists=exists,
            db_name=site_config.get("db_name", ""),
            db_host=site_config.get("db_host") or "localhost",
            db_type=site_config.get("db_type") or "mariadb",
            installed_apps=installed_apps,
            site_config=site_config,
            broken=broken,
            provisioning=is_provisioning,
        )
