from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.site.migration_backup import SiteMigrationBackup


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
