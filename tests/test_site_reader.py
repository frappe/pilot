from __future__ import annotations

import json
from pathlib import Path

from admin.backend.readers.site_reader import SiteReader


def _make_site(sites: Path, name: str, config: dict) -> None:
    site_dir = sites / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps(config))


def test_site_reader_exposes_db_type(tmp_path: Path) -> None:
    sites = tmp_path / "sites"
    _make_site(sites, "pg.localhost", {"db_type": "postgres", "installed_apps": ["frappe"]})
    # frappe omits db_type for older MariaDB sites — reader defaults to mariadb.
    _make_site(sites, "old.localhost", {"installed_apps": ["frappe"]})

    infos = {s.name: s for s in SiteReader(tmp_path).read_all()}

    assert infos["pg.localhost"].db_type == "postgres"
    assert infos["old.localhost"].db_type == "mariadb"
