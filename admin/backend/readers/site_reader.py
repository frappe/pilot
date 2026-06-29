from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pilot.commands.list_site_apps import _query_via_db_cli


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


class SiteReader:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def read_all(self) -> list[SiteInfo]:
        sites_path = self._bench_root / "sites"
        if not sites_path.is_dir():
            return []
        return [
            self._read_site(d.name)
            for d in sorted(sites_path.iterdir())
            if d.is_dir() and (d / "site_config.json").exists()
        ]

    def read_one(self, site_name: str) -> SiteInfo:
        return self._read_site(site_name)

    def _read_site(self, site_name: str) -> SiteInfo:
        site_config_path = self._bench_root / "sites" / site_name / "site_config.json"
        exists = site_config_path.exists()
        site_config: dict = {}

        if exists:
            try:
                site_config = json.loads(site_config_path.read_text())
            except (json.JSONDecodeError, OSError):
                site_config = {}

        installed_apps: list[str] = []
        broken = False

        if exists:
            if isinstance(site_config.get("installed_apps"), list):
                installed_apps = site_config["installed_apps"]
            else:
                apps = _query_via_db_cli(site_config)
                if apps is not None:
                    installed_apps = apps
                else:
                    broken = True

        return SiteInfo(
            name=site_name,
            exists=exists,
            db_name=site_config.get("db_name", ""),
            db_host=site_config.get("db_host") or "localhost",
            # frappe omits db_type for older MariaDB sites; default accordingly.
            db_type=site_config.get("db_type") or "mariadb",
            installed_apps=installed_apps,
            site_config=site_config,
            broken=broken,
        )
