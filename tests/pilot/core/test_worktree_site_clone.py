"""Cloning a SQLite site into a worktree."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from pilot.core.worktree.site_clone import SiteClone
from pilot.exceptions import BenchError


def _make_site(path: Path) -> sqlite3.Connection:
    for name in ("db", "public/files", "private/files", "private/backups"):
        (path / name).mkdir(parents=True)
    (path / "public" / "files" / "logo.png").write_text("logo")
    (path / "private" / "files" / "report.pdf").write_text("report")
    (path / "private" / "backups" / "old.sql.gz").write_text("backup")
    (path / "site_config.json").write_text(
        json.dumps(
            {
                "db_name": "_source",
                "db_type": "sqlite",
                "encryption_key": "key",
                "pilot_auth_token": "token",
                "pilot_endpoint": "http://admin",
                "host_name": "https://gp.example.com",
                "domains": ["gp.example.com"],
                "ssl": True,
            }
        )
    )
    connection = sqlite3.connect(path / "db" / "_source.db")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE note (body TEXT)")
    connection.executemany("INSERT INTO note VALUES (?)", [("one",), ("two",)])
    connection.commit()
    return connection


def test_clone_copies_uncheckpointed_writes_and_rewrites_the_config(tmp_path: Path) -> None:
    source = tmp_path / "gp.localhost"
    target = tmp_path / "overlay" / "feature-x.gp.localhost"
    writer = _make_site(source)
    assert (source / "db" / "_source.db-wal").stat().st_size > 0

    SiteClone(source, target, "sqlite").run()
    writer.close()

    config = json.loads((target / "site_config.json").read_text())
    assert config["db_name"] != "_source"
    assert config["developer_mode"] == 1
    assert (config["mute_emails"], config["pause_scheduler"]) == (1, 1)
    assert config["encryption_key"] == "key"
    for key in ("pilot_auth_token", "pilot_endpoint", "host_name", "domains", "ssl"):
        assert key not in config
    with closing(sqlite3.connect(target / "db" / f"{config['db_name']}.db")) as clone:
        assert clone.execute("SELECT body FROM note ORDER BY body").fetchall() == [("one",), ("two",)]
    assert (target / "public" / "files" / "logo.png").exists()
    assert (target / "private" / "files" / "report.pdf").exists()
    assert not (target / "private" / "backups").exists()
    assert (target / "locks").is_dir()


@pytest.mark.parametrize("db_type", ["mariadb", "postgres"])
def test_clone_refuses_server_databases(tmp_path: Path, db_type: str) -> None:
    with pytest.raises(BenchError, match="not supported yet"):
        SiteClone(tmp_path / "source", tmp_path / "target", db_type)
    assert not (tmp_path / "target").exists()
