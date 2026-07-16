from __future__ import annotations

import json
from pathlib import Path

import pytest

from admin.backend.readers.sites import SiteReader


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


def test_site_reader_skips_symlinked_site(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _make_site(outside, "linked.localhost", {"installed_apps": ["frappe"]})
    sites = tmp_path / "sites"
    sites.mkdir()
    (sites / "linked.localhost").symlink_to(
        outside / "linked.localhost", target_is_directory=True
    )

    assert SiteReader(tmp_path).read_all() == []


def test_site_reader_refuses_site_path_outside_bench(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="within the bench"):
        SiteReader(tmp_path).read_one("../outside")
