"""Tests for admin.backend.readers.backup_reader.dump_engine."""

from __future__ import annotations

import gzip
from pathlib import Path

from admin.backend.readers.backup_reader import dump_engine

_MARIADB = "-- begin frappe metadata\n-- end frappe metadata\n/*!40101 SET NAMES utf8mb4 */;\n-- MariaDB dump 10.19\nCREATE TABLE `tabUser` (\n  `name` varchar(140) NOT NULL\n);\n"
_POSTGRES = '-- begin frappe metadata\n-- end frappe metadata\n--\n-- PostgreSQL database dump\n--\nCREATE TABLE public."tabUser" (\n  "name" varchar(140) NOT NULL\n);\n'


def _gz(path: Path, text: str) -> Path:
    with gzip.open(path, "wt") as f:
        f.write(text)
    return path


def test_dump_engine_detects_mariadb_gz(tmp_path: Path) -> None:
    assert dump_engine(_gz(tmp_path / "m.sql.gz", _MARIADB)) == "mariadb"


def test_dump_engine_detects_postgres_gz(tmp_path: Path) -> None:
    assert dump_engine(_gz(tmp_path / "p.sql.gz", _POSTGRES)) == "postgres"


def test_dump_engine_detects_plain_sql(tmp_path: Path) -> None:
    path = tmp_path / "m.sql"
    path.write_text(_MARIADB)
    assert dump_engine(path) == "mariadb"
