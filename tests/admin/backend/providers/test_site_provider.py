from __future__ import annotations

import json
from pathlib import Path

import pytest

from admin.backend.providers.sites import SiteProvider
from pilot.core.database.base import QueryResult
from pilot.exceptions import DatabaseError


class _FakeDatabase:
    """Answers the Singles probe only - anything else reads as a table this site lacks."""

    def __init__(self, rows: list[list]) -> None:
        self._rows = rows

    def quote_identifier(self, name: str) -> str:
        return f'"{name}"'

    def execute(self, query: str, read_only: bool = True) -> QueryResult:
        if "tabSingles" not in query:
            raise DatabaseError("no such table")
        return QueryResult(columns=["value"], rows=self._rows, duration_ms=0.0)


def _make_site(sites: Path, name: str, config: dict) -> None:
    site_dir = sites / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps(config))


def test_site_provider_exposes_db_type(tmp_path: Path) -> None:
    sites = tmp_path / "sites"
    _make_site(sites, "pg.localhost", {"db_type": "postgres", "installed_apps": ["frappe"]})
    # frappe omits db_type for older MariaDB sites - provider defaults to mariadb.
    _make_site(sites, "old.localhost", {"installed_apps": ["frappe"]})

    infos = {s.name: s for s in SiteProvider(tmp_path).get_all()}

    assert infos["pg.localhost"].db_type == "postgres"
    assert infos["old.localhost"].db_type == "mariadb"


def test_site_provider_skips_symlinked_site(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _make_site(outside, "linked.localhost", {"installed_apps": ["frappe"]})
    sites = tmp_path / "sites"
    sites.mkdir()
    (sites / "linked.localhost").symlink_to(outside / "linked.localhost", target_is_directory=True)

    assert SiteProvider(tmp_path).get_all() == []


def test_site_provider_refuses_site_path_outside_bench(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="within the bench"):
        SiteProvider(tmp_path).get_one("../outside")


def test_site_provider_defaults_setup_complete_false_without_db_access(tmp_path: Path) -> None:
    sites = tmp_path / "sites"
    _make_site(sites, "site.localhost", {"installed_apps": ["frappe"]})

    info = SiteProvider(tmp_path).get_one("site.localhost")

    assert info.setup_complete is False


def test_site_provider_reads_setup_complete_for_postgres_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sites = tmp_path / "sites"
    _make_site(
        sites,
        "pg.localhost",
        {
            "db_type": "postgres",
            "db_name": "site_db",
            "db_user": "site_db",
            "db_password": "secret",
            "db_port": 5432,
            "installed_apps": ["frappe"],
        },
    )
    monkeypatch.setattr(
        "pilot.core.site.config.database_state.make_site_database",
        lambda *args, **kwargs: _FakeDatabase([["1"]]),
    )

    info = SiteProvider(tmp_path).get_one("pg.localhost")

    assert info.setup_complete is True
