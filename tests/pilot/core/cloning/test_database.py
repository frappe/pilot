from __future__ import annotations

import os
import sys
from contextlib import nullcontext
from threading import Barrier
from types import SimpleNamespace

import pytest

from pilot.core.bench.cloning.bench import BenchClone
from pilot.core.site.clone import SiteClone
from pilot.core.site.clone_database import SiteDatabaseClone, stream_database
from pilot.exceptions import BenchError, CommandError


@pytest.mark.parametrize("failure", [None, "database", "environment", "uploads", "multiple"])
def test_fork_overlaps_database_and_environment_and_waits_before_cleanup(source, monkeypatch, failure):
    from threading import Event

    started, preparing, completed = Event(), Event(), Event()
    files_completed = Event()
    original = BenchClone.prepare_environment
    copy_files = SiteClone.copy_files
    cleaned = []
    progress = []

    def import_database(self):
        started.set()
        assert preparing.wait(5), "Database import did not overlap environment preparation"
        assert files_completed.wait(5), "Uploads did not overlap database import"
        completed.set()
        if failure in ("database", "multiple"):
            raise RuntimeError("database failed")

    def copy_uploads(self, site):
        try:
            assert started.wait(5), "Database import did not overlap upload copying"
            if failure in ("uploads", "multiple"):
                raise RuntimeError("uploads failed")
            copy_files(self, site)
        finally:
            files_completed.set()

    def prepare(self, destination, rebuild, on_progress):
        assert started.wait(5), "Database import was not started before environment preparation"
        preparing.set()
        if failure in ("environment", "multiple"):
            raise RuntimeError("environment failed")
        original(self, destination, rebuild, on_progress)

    def cleanup(self):
        assert completed.is_set(), "Cleanup raced with the database import"
        assert files_completed.is_set(), "Cleanup raced with upload copying"
        cleaned.append(self.config["db_name"])

    monkeypatch.setattr(BenchClone, "prepare_environment", prepare)
    monkeypatch.setattr(SiteDatabaseClone, "run", import_database)
    monkeypatch.setattr(SiteDatabaseClone, "cleanup", cleanup)
    monkeypatch.setattr(SiteClone, "finish", lambda *args: None)
    monkeypatch.setattr(SiteClone, "copy_files", copy_uploads)
    monkeypatch.setattr("pilot.core.bench.cloning.runtime.ForkRuntime", lambda *args, **kwargs: nullcontext())
    if failure:
        expected = "environment" if failure == "multiple" else failure
        with pytest.raises(RuntimeError, match=f"{expected} failed") as raised:
            source.fork("parallel", on_progress=progress.append)
        if failure == "multiple":
            assert raised.value.__notes__ == [
                "Database import failed: database failed",
                "Upload copying failed: uploads failed",
            ]
            assert all(note in progress for note in raised.value.__notes__)
        assert len(cleaned) == 1
        assert not (source.path.parent / "parallel/sites/parallel.localhost").exists()
    else:
        destination = source.fork("parallel", on_progress=lambda message: None)
        assert destination.site("parallel.localhost").exists
        assert cleaned == []


@pytest.mark.parametrize("dump_exit,import_exit", [(1, 0), (0, 1), (1, 1)])
def test_stream_reports_failure_of_either_process(dump_exit, import_exit):
    dump = [sys.executable, "-c", f"import sys; print('sql'); sys.exit({dump_exit})"]
    restore = [sys.executable, "-c", f"import sys; sys.stdin.read(); sys.exit({import_exit})"]
    with pytest.raises(BenchError, match="Database clone failed"):
        stream_database(dump, restore, os.environ.copy(), os.environ.copy())


def test_stream_transfers_large_input_without_a_dump_file(tmp_path):
    target = tmp_path / "received"
    dump = [sys.executable, "-c", "import sys; sys.stdout.write('sql' * 1_000_000)"]
    restore = [
        sys.executable,
        "-c",
        "import sys; from pathlib import Path; Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())",
        str(target),
    ]
    stream_database(dump, restore, os.environ.copy(), os.environ.copy())
    assert target.stat().st_size == 3_000_000
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("engine", ["mariadb", "postgres"])
def test_clone_database_cleanup_only_drops_generated_destination(source, monkeypatch, engine):
    from pilot.managers.database import MariaDBManager

    statements = []
    monkeypatch.setattr(MariaDBManager, "run_admin_sql", lambda self, sql: statements.append(sql))
    monkeypatch.setattr(SiteDatabaseClone, "postgres_admin_sql", lambda self, sql: statements.append(sql))
    name = "_0123456789abcdef"
    clone = SiteDatabaseClone(
        source.site("source.localhost"), source.site("copy.localhost"), {"db_name": name, "db_type": engine}
    )
    clone.cleanup()
    assert len(statements) == 1
    assert "DROP DATABASE IF EXISTS" in statements[0]
    assert ("DROP ROLE IF EXISTS" if engine == "postgres" else "DROP USER IF EXISTS") in statements[0]
    assert statements[0].count(name) == 2
    clone.config["db_name"] = "source_db"
    with pytest.raises(BenchError, match="not generated"):
        clone.cleanup()
    assert len(statements) == 1


@pytest.mark.parametrize(
    "config, expected",
    [
        ({"db_host": "remote.example", "db_port": 3307}, ["--host", "remote.example", "--port", "3307"]),
        ({"db_socket": "/tmp/site.sock", "db_host": "remote.example"}, ["--socket", "/tmp/site.sock"]),
        ({}, ["--socket", "/tmp/bench.sock"]),
    ],
)
def test_database_clone_respects_site_connection_settings(source, config, expected):
    source.config.mariadb.socket_path = "/tmp/bench.sock"
    assert SiteDatabaseClone.mysql_args({"db_name": "site_db", **config}, source) == [
        "--user",
        "site_db",
        *expected,
    ]


def test_postgres_clone_uses_resolved_client_binaries(source, monkeypatch):
    source.config.db_type = "postgres"
    original = source.site("source.localhost")
    destination = source.site("uat.localhost")
    config = {"db_name": "_target", "db_password": "target-secret"}
    sql_calls = []
    streams = []
    monkeypatch.setattr(
        "pilot.managers.database.PostgresManager.client_binary", lambda self, name: f"/brew/bin/{name}"
    )
    monkeypatch.setattr(
        "pilot.core.site.clone_database.subprocess.run", lambda argv, **kwargs: sql_calls.append(argv)
    )
    monkeypatch.setattr("pilot.core.site.clone_database.stream_database", lambda *args: streams.append(args))
    SiteDatabaseClone(original, destination, config).postgres(
        {"db_name": "source_db", "db_password": "source-secret"}
    )
    assert sql_calls[0][0] == "/brew/bin/psql"
    assert streams[0][0][0] == "/brew/bin/pg_dump"
    assert streams[0][1][0] == "/brew/bin/psql"


@pytest.mark.parametrize(
    "unsupported", [None, "relationships", "view", "engine", "small", "escaped", "probe"]
)
def test_mariadb_clone_parallel_schema_then_single_snapshot_or_full_fallback(
    source, monkeypatch, unsupported
):
    tables = [f"table_{index}" for index in range(16)]
    rows = [
        f"{name}\tBASE TABLE\t{'MyISAM' if index == 0 else 'InnoDB'}" for index, name in enumerate(tables)
    ]
    rows.append("counter\tSEQUENCE\tInnoDB")
    if unsupported == "view":
        rows.append("view\tVIEW\tNULL")
    elif unsupported == "engine":
        rows[0] = "table_0\tBASE TABLE\tMEMORY"
    elif unsupported == "small":
        rows = rows[:15]
    elif unsupported == "escaped":
        rows[0] = "table\\tname\tBASE TABLE\tInnoDB"
    metadata = "\n".join(["1" if unsupported == "relationships" else "0", *rows]).encode()

    def probe(*args, **kwargs):
        if unsupported == "probe":
            raise CommandError("Metadata access denied")
        return SimpleNamespace(stdout=metadata)

    monkeypatch.setattr("pilot.core.site.clone_database.run_command", probe)
    monkeypatch.setattr("pilot.managers.database.MariaDBManager.run_admin_sql", lambda *args: None)
    streams = []
    schema_ready = Barrier(4)

    def stream(dump, restore, source_env, target_env):
        assert "source-secret" not in dump and "target-secret" not in restore
        assert source_env["MYSQL_PWD"] == "source-secret"
        assert target_env["MYSQL_PWD"] == "target-secret"
        assert "--single-transaction" in dump
        if "--skip-add-drop-table" in dump:
            schema_ready.wait(timeout=5)
        elif unsupported is None and "--no-create-info" in dump:
            assert len(streams) == 4
        elif unsupported is None:
            assert len(streams) == 5 and "--no-create-info" in streams[-1]
        streams.append(dump)

    monkeypatch.setattr("pilot.core.site.clone_database.stream_database", stream)
    clone = SiteDatabaseClone(
        source.site("source.localhost"),
        source.site("copy.localhost"),
        {"db_name": "_0123456789abcdef", "db_password": "target-secret", "db_type": "mariadb"},
    )
    clone.run()
    if unsupported:
        assert len(streams) == 1 and "--no-data" not in streams[0]
        assert streams[0][-1] == "source_db"
    else:
        copied = [table for dump in streams[:4] for table in dump[dump.index("--") + 2 :]]
        assert sorted(copied) == sorted(tables)
        assert all("--no-data" in dump for dump in streams[:4])
        assert len(streams) == 6 and streams[4][-1] == "source_db"
        assert streams[5][-1] == "counter" and "--no-data" in streams[5]
