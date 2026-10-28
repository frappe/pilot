from pathlib import Path
from subprocess import CompletedProcess, Popen
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.site.migration_backup import SiteMigrationBackup
from pilot.exceptions import BenchError


@pytest.mark.parametrize("database_engine", ["sqlite", "postgres"])
def test_non_mariadb_snapshot_uses_frappe_full_database_backup(
    tmp_path: Path,
    database_engine: str,
) -> None:
    site = migration_site(tmp_path, database_engine)
    backup = SiteMigrationBackup(site)

    def complete_backup(argv, **kwargs):
        database_path = Path(argv[argv.index("--backup-path-db") + 1])
        config_path = Path(argv[argv.index("--backup-path-conf") + 1])
        database_path.write_bytes(b"database")
        config_path.write_text("{}")
        return CompletedProcess(argv, 0)

    with patch("pilot.core.site.migration_backup.run_command", side_effect=complete_backup) as run:
        previous_tables = backup.create("operation-1")

    argv = run.call_args.args[0]
    assert previous_tables == []
    assert argv == [
        "bench",
        "frappe",
        "--site",
        "site1.localhost",
        "backup",
        "--backup-path-db",
        str(backup.database_backup_path),
        "--backup-path-conf",
        str(backup.config_backup_path),
        "--ignore-backup-conf",
    ]
    assert run.call_args.kwargs == {"cwd": site.bench.sites_path, "stream_output": True}
    assert backup.exists


@pytest.mark.parametrize("database_engine", ["sqlite", "postgres"])
def test_non_mariadb_restore_replaces_the_whole_database(
    tmp_path: Path,
    database_engine: str,
) -> None:
    site = migration_site(tmp_path, database_engine)
    backup = SiteMigrationBackup(site)
    backup.directory.mkdir()
    backup.database_backup_path.write_bytes(b"database")

    backup.restore(["tabUser"])

    site.restore.assert_called_once_with(str(backup.database_backup_path))


def migration_site(tmp_path: Path, database_engine: str) -> MagicMock:
    site = MagicMock()
    site.path = tmp_path / "sites" / "site1.localhost"
    site.path.mkdir(parents=True)
    (site.path / "site_config.json").write_text(
        f'{{"db_type": "{database_engine}", "db_name": "site_db"}}'
    )
    site.config.name = "site1.localhost"
    site.bench.sites_path = tmp_path / "sites"
    site.bench.migrations.unresolved_for_site.return_value = [MagicMock(id="operation-1")]
    site._frappe_call.side_effect = lambda *args: ["bench", *args]
    return site


@pytest.mark.parametrize("rows", [1, 50000])
def test_a_failed_table_import_says_why(tmp_path: Path, rows: int) -> None:
    import gzip

    site = migration_site(tmp_path, "mariadb")
    backup = SiteMigrationBackup(site)
    dump = tmp_path / "tabUser.sql.gz"
    with gzip.open(dump, "wb") as compressed:
        compressed.write(b"INSERT INTO x VALUES (1);\n" * rows)
    client = tmp_path / "mariadb"
    client.write_text("#!/bin/sh\necho \"ERROR 2002 (HY000): Can't connect to server\" >&2\nexit 1\n")
    client.chmod(0o755)
    def exited_client(*args, **kwargs):
        process = Popen(*args, **kwargs)
        process.wait(timeout=5)
        return process

    with (
        patch.object(SiteMigrationBackup, "_cli", return_value=str(client)),
        patch.object(SiteMigrationBackup, "_conn_args", return_value=[]),
        patch("pilot.core.site.migration_backup.subprocess.Popen", side_effect=exited_client),
        pytest.raises(BenchError, match=r"Failed to import tabUser\.sql\.gz .*Can.t connect to server"),
    ):
        backup._import_dump({}, "site_db", dump)


@pytest.mark.parametrize("inventory", [None, "{", "{}", "[]", "[42]", '["tabUser", null]'])
def test_restore_rejects_a_missing_or_invalid_table_inventory(tmp_path: Path, inventory) -> None:
    site = migration_site(tmp_path, "mariadb")
    backup = SiteMigrationBackup(site)
    backup.directory.mkdir()
    if inventory is not None:
        backup.previous_tables_path.write_text(inventory)

    with (
        patch.object(backup, "_list_tables", return_value=["tabUser"]) as list_tables,
        patch.object(backup, "_import_dump") as import_dump,
        patch.object(backup, "_drop_tables") as drop_tables,
        pytest.raises(BenchError, match="table inventory"),
    ):
        backup.restore([])

    list_tables.assert_not_called()
    import_dump.assert_not_called()
    drop_tables.assert_not_called()


def test_restore_checks_all_required_dumps_before_it_imports_any(tmp_path: Path) -> None:
    site = migration_site(tmp_path, "mariadb")
    backup = SiteMigrationBackup(site)
    backup.directory.mkdir()
    backup.previous_tables_path.write_text('["tabUser", "tabDocType"]')
    backup._dump_path("tabUser").write_bytes(b"dump")

    with (
        patch.object(backup, "_import_dump") as import_dump,
        patch.object(backup, "_drop_tables") as drop_tables,
        patch.object(backup, "_list_tables", return_value=["tabUser", "tabDocType"]),
        pytest.raises(BenchError, match=r"tabDocType\.sql\.gz"),
    ):
        backup.restore(["tabUser", "tabDocType"])

    import_dump.assert_not_called()
    drop_tables.assert_not_called()


def test_restore_drops_new_tables_without_requiring_a_dump_for_them(tmp_path: Path) -> None:
    site = migration_site(tmp_path, "mariadb")
    backup = SiteMigrationBackup(site)
    backup.directory.mkdir()
    backup.previous_tables_path.write_text('["tabUser"]')
    backup._dump_path("tabUser").write_bytes(b"dump")

    with (
        patch.object(backup, "_import_dump") as import_dump,
        patch.object(backup, "_drop_tables") as drop_tables,
        patch.object(backup, "_list_tables", return_value=["tabUser", "tabNew"]),
    ):
        backup.restore(["tabUser", "tabNew"])

    import_dump.assert_called_once_with(
        {"db_type": "mariadb", "db_name": "site_db"}, "site_db", backup._dump_path("tabUser")
    )
    drop_tables.assert_called_once_with({"db_type": "mariadb", "db_name": "site_db"}, "site_db", ["tabNew"])
