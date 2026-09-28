"""Cloning a site into a worktree, on SQLite and on a database server, and dropping its database."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from pilot.commands.worktree.add import AddWorktreeCommand
from pilot.config import BenchConfig, MariaDBConfig, PostgresConfig
from pilot.core.bench import Bench
from pilot.core.worktree import Worktree
from pilot.core.worktree.site_clone import SiteClone
from pilot.core.worktree.site_database import (
    ClientCommand,
    MariaDBSiteDatabase,
    PostgresSiteDatabase,
    SQLiteSiteDatabase,
    pipe,
)
from pilot.exceptions import BenchError
from pilot.internal.git import GitRepo
from tests.pilot.core.test_worktree import make_bench

CLONE_DB = "_0123456789abcdef"


class ServerRecorder:
    """Stands in for the database server: records admin SQL and piped client commands."""

    def __init__(self) -> None:
        self.sql: list[str] = []
        self.pipes: list[tuple[ClientCommand, ClientCommand]] = []


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> ServerRecorder:
    recorder = ServerRecorder()
    for manager in ("MariaDBManager", "PostgresManager"):
        monkeypatch.setattr(
            f"pilot.managers.database.{manager}.run_admin_sql",
            lambda self, sql, timeout=5: recorder.sql.append(sql),
        )
    monkeypatch.setattr(
        "pilot.core.worktree.site_database.pipe", lambda dump, load: recorder.pipes.append((dump, load))
    )
    monkeypatch.setattr("pilot.core.worktree.site_database.which", lambda name: f"/usr/bin/{name}")
    return recorder


def _make_site(path: Path, config: dict) -> None:
    for name in ("db", "public/files", "private/files", "private/backups"):
        (path / name).mkdir(parents=True)
    (path / "public" / "files" / "logo.png").write_text("logo")
    (path / "private" / "files" / "report.pdf").write_text("report")
    (path / "private" / "backups" / "old.sql.gz").write_text("backup")
    identity = {
        "encryption_key": "key",
        "pilot_auth_token": "token",
        "pilot_endpoint": "http://admin",
        "host_name": "https://gp.example.com",
        "domains": ["gp.example.com"],
        "ssl": True,
    }
    (path / "site_config.json").write_text(json.dumps({**config, **identity}))


def _assert_clone_config_and_files(target: Path) -> dict:
    config = json.loads((target / "site_config.json").read_text())
    assert config["db_name"] == CLONE_DB
    assert config["developer_mode"] == 1
    assert (config["mute_emails"], config["pause_scheduler"]) == (1, 1)
    assert config["encryption_key"] == "key"
    for key in ("pilot_auth_token", "pilot_endpoint", "host_name", "domains", "ssl", "db_user"):
        assert key not in config
    assert (target / "public" / "files" / "logo.png").exists()
    assert (target / "private" / "files" / "report.pdf").exists()
    assert not (target / "private" / "backups").exists()
    assert (target / "locks").is_dir()
    return config


def test_sqlite_clone_copies_uncheckpointed_writes_and_rewrites_the_config(tmp_path: Path) -> None:
    source = tmp_path / "gp.localhost"
    target = tmp_path / "overlay" / "feature-x.gp.localhost"
    _make_site(source, {"db_name": "_source", "db_type": "sqlite"})
    writer = sqlite3.connect(source / "db" / "_source.db")
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE note (body TEXT)")
    writer.executemany("INSERT INTO note VALUES (?)", [("one",), ("two",)])
    writer.commit()
    assert (source / "db" / "_source.db-wal").stat().st_size > 0

    SiteClone(source, target, SQLiteSiteDatabase(), CLONE_DB).run()
    writer.close()

    _assert_clone_config_and_files(target)
    with closing(sqlite3.connect(target / "db" / f"{CLONE_DB}.db")) as clone:
        assert clone.execute("SELECT body FROM note ORDER BY body").fetchall() == [("one",), ("two",)]


@pytest.mark.parametrize(
    ("database", "db_type", "password_variable", "created", "dump_binary", "load_binary"),
    [
        (
            MariaDBSiteDatabase(MariaDBConfig(host="127.0.0.1", port=3321)),
            "mariadb",
            "MYSQL_PWD",
            [f"CREATE DATABASE `{CLONE_DB}`", f"CREATE USER '{CLONE_DB}'@'%'", f"TO '{CLONE_DB}'@'%'"],
            "/usr/bin/mariadb-dump",
            "/usr/bin/mariadb",
        ),
        (
            PostgresSiteDatabase(PostgresConfig(host="127.0.0.1", port=5451)),
            "postgres",
            "PGPASSWORD",
            [f'CREATE ROLE "{CLONE_DB}" WITH LOGIN', f'CREATE DATABASE "{CLONE_DB}" OWNER "{CLONE_DB}"'],
            "/usr/bin/pg_dump",
            "/usr/bin/psql",
        ),
    ],
)
def test_server_clone_gets_its_own_database_and_account_and_streams_the_base_data_into_it(
    tmp_path: Path,
    server: ServerRecorder,
    database: MariaDBSiteDatabase | PostgresSiteDatabase,
    db_type: str,
    password_variable: str,
    created: list[str],
    dump_binary: str,
    load_binary: str,
) -> None:
    source = tmp_path / "gp.localhost"
    target = tmp_path / "overlay" / "feature-x.gp.localhost"
    base = {"db_type": db_type, "db_name": "_base", "db_user": "_base", "db_password": "base-secret"}
    _make_site(source, {**base, "db_host": "127.0.0.1", "db_port": 3321})

    SiteClone(source, target, database, CLONE_DB).run()

    config = _assert_clone_config_and_files(target)
    assert config["db_password"] not in ("", "base-secret")
    assert (config["db_host"], config["db_port"]) == ("127.0.0.1", 3321)
    [sql] = server.sql
    for statement in created:
        assert statement in sql
    assert config["db_password"] in sql
    [(dump, load)] = server.pipes
    assert dump.argv[0] == dump_binary
    assert load.argv[0] == load_binary
    assert "_base" in dump.argv[-1] and dump.env == {password_variable: "base-secret"}
    assert CLONE_DB in load.argv[-1] and load.env == {password_variable: config["db_password"]}
    assert any("--user=_base" in arg or "--username=_base" in arg for arg in dump.argv)
    assert any(f"--user={CLONE_DB}" in arg or f"--username={CLONE_DB}" in arg for arg in load.argv)
    for command in (dump, load):
        assert not any("secret" in arg or config["db_password"] in arg for arg in command.argv)


@pytest.mark.parametrize(
    "database",
    [MariaDBSiteDatabase(MariaDBConfig()), PostgresSiteDatabase(PostgresConfig())],
)
def test_server_database_refuses_a_name_pilot_did_not_generate(
    server: ServerRecorder, database: MariaDBSiteDatabase | PostgresSiteDatabase
) -> None:
    with pytest.raises(BenchError, match="did not generate"):
        database.drop("production")
    database.drop(CLONE_DB)

    assert len(server.sql) == 1
    assert CLONE_DB in server.sql[0]


@pytest.mark.parametrize(
    ("dump", "load", "message"),
    [
        (
            ["sh", "-c", "echo dump broke >&2; exit 3"],
            ["cat"],
            "Dumping the base site's database failed: dump broke",
        ),
        (
            ["echo", "rows"],
            ["sh", "-c", "cat >/dev/null; echo load broke >&2; exit 4"],
            "Loading.*load broke",
        ),
    ],
)
def test_pipe_fails_when_either_side_fails(dump: list[str], load: list[str], message: str) -> None:
    with pytest.raises(BenchError, match=message):
        pipe(ClientCommand(dump), ClientCommand(load))


def test_pipe_streams_the_dump_into_the_load(tmp_path: Path) -> None:
    output = tmp_path / "loaded.sql"

    pipe(
        ClientCommand(["sh", "-c", 'printf "%s" "$SECRET"'], {"SECRET": "rows"}),
        ClientCommand(["sh", "-c", f"cat > {output}"]),
    )

    assert output.read_text() == "rows"


def _server_bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_type: str) -> Bench:
    monkeypatch.setattr("pilot.core.bench.ports._port_is_live", lambda port: False)
    monkeypatch.setattr(Worktree, "build_assets", lambda self: None)
    bench = make_bench(tmp_path)
    with BenchConfig.open(bench.path) as config:
        config.db_type = db_type
    site = {
        "db_type": db_type,
        "db_name": "_base",
        "db_password": "base-secret",
        "installed_apps": ["gameplan"],
    }
    _make_site(bench.sites_path / "gp.localhost", site)
    return Bench(bench.path)


def _add(bench: Bench, name: str) -> Worktree:
    AddWorktreeCommand(bench=bench, app_name="gameplan", worktree_name=name).run()
    return Bench(bench.path).worktree(name)


@pytest.mark.parametrize("db_type", ["mariadb", "postgres"])
def test_remove_and_a_failed_add_drop_exactly_the_recorded_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: ServerRecorder, db_type: str
) -> None:
    bench = _server_bench(tmp_path, monkeypatch, db_type)
    worktree = _add(bench, "feature-x")
    site_config = json.loads(
        (worktree.runtime_bench.sites_path / worktree.site_name / "site_config.json").read_text()
    )
    assert site_config["db_name"] == worktree.config.db_name
    server.sql.clear()

    worktree.remove(force=True)

    [drop] = server.sql
    assert drop.startswith("DROP DATABASE IF EXISTS") and drop.count(worktree.config.db_name) == 2
    assert "_base" not in drop

    def fail(self: Worktree) -> None:
        raise BenchError("build failed")

    monkeypatch.setattr(Worktree, "build_assets", fail)
    server.sql.clear()
    with pytest.raises(BenchError, match="build failed"):
        _add(bench, "broken")

    created, dropped = server.sql
    db_name = created.split()[2].strip('`"')
    assert dropped.startswith("DROP DATABASE IF EXISTS") and dropped.count(db_name) == 2
    assert BenchConfig.read(bench.path).worktrees == []
    assert not (bench.path / "worktrees" / "broken").exists()


@pytest.mark.parametrize("db_type", ["mariadb", "postgres"])
def test_a_failed_drop_leaves_the_worktree_in_place_and_remove_can_run_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: ServerRecorder, db_type: str
) -> None:
    bench = _server_bench(tmp_path, monkeypatch, db_type)
    worktree = _add(bench, "feature-x")
    manager = "MariaDBManager" if db_type == "mariadb" else "PostgresManager"

    def lose_connection(self: object, sql: str, timeout: int = 5) -> None:
        # The database is gone, but the account drop never ran.
        server.sql.append(sql)
        raise subprocess.CalledProcessError(1, "sql", stderr="Lost connection to server")

    monkeypatch.setattr(f"pilot.managers.database.{manager}.run_admin_sql", lose_connection)
    with pytest.raises(BenchError, match="Lost connection to server"):
        worktree.remove(force=True)

    assert worktree.app_path.exists()
    assert (worktree.runtime_bench.sites_path / worktree.site_name / "site_config.json").exists()
    assert BenchConfig.read(bench.path).worktrees == [worktree.config]

    monkeypatch.setattr(
        f"pilot.managers.database.{manager}.run_admin_sql",
        lambda self, sql, timeout=5: server.sql.append(sql),
    )
    server.sql.clear()
    Bench(bench.path).worktree("feature-x").remove(force=True)

    [drop] = server.sql
    assert drop.count("IF EXISTS") == 2
    assert not worktree.path.exists()
    assert BenchConfig.read(bench.path).worktrees == []


def test_remove_refuses_when_the_site_config_names_another_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: ServerRecorder
) -> None:
    bench = _server_bench(tmp_path, monkeypatch, "mariadb")
    worktree = _add(bench, "feature-x")
    config_path = worktree.runtime_bench.sites_path / worktree.site_name / "site_config.json"
    config_path.write_text(json.dumps({**json.loads(config_path.read_text()), "db_name": "_base"}))
    server.sql.clear()

    with pytest.raises(BenchError, match=f"records database '{worktree.config.db_name}'.*names '_base'"):
        worktree.remove(force=True)

    assert server.sql == []
    assert worktree.app_path.exists()
    assert BenchConfig.read(bench.path).worktrees == [worktree.config]
    assert GitRepo(bench.apps_path / "gameplan").has_branch("feature-x")


def test_remove_checks_the_checkout_after_stop_and_drops_the_database_before_removing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: ServerRecorder
) -> None:
    bench = _server_bench(tmp_path, monkeypatch, "mariadb")
    worktree = _add(bench, "feature-x")
    site_config = worktree.runtime_bench.sites_path / worktree.site_name / "site_config.json"
    stopped: list[str] = []

    def stop_and_write(self: Worktree) -> None:
        # A process writes to the checkout before it exits, like gameplan's type generator.
        (self.app_path / "doctypes.ts").write_text("generated")
        stopped.append(self.config.name)

    monkeypatch.setattr(Worktree, "is_running", property(lambda self: not stopped))
    monkeypatch.setattr(Worktree, "stop", stop_and_write)
    server.sql.clear()

    with pytest.raises(BenchError, match=r"uncommitted changes:\n  \?\? doctypes.ts"):
        worktree.remove()

    assert stopped == ["feature-x"]
    assert server.sql == []
    assert (worktree.app_path / "doctypes.ts").exists()
    assert site_config.exists()
    assert BenchConfig.read(bench.path).worktrees == [worktree.config]

    (worktree.app_path / "doctypes.ts").unlink()
    checkout_at_drop: list[bool] = []
    monkeypatch.setattr(
        "pilot.managers.database.MariaDBManager.run_admin_sql",
        lambda self, sql, timeout=5: checkout_at_drop.append(worktree.app_path.exists()),
    )
    Bench(bench.path).worktree("feature-x").remove(delete_branch=True)

    assert checkout_at_drop == [True]
    assert not worktree.path.exists()
    assert BenchConfig.read(bench.path).worktrees == []
    assert not GitRepo(bench.apps_path / "gameplan").has_branch("feature-x")
