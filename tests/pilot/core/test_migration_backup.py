import json
from pathlib import Path
from subprocess import CompletedProcess, Popen
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.site.config.file import SiteConfigFile
from pilot.core.site.migration_backup import SiteMigrationBackup
from pilot.exceptions import BenchError
from pilot.integrations.s3.base import S3IntegrationError


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
    (site.path / "site_config.json").write_text(f'{{"db_type": "{database_engine}", "db_name": "site_db"}}')
    site.config_file = SiteConfigFile(site.path.parent, site.path.name)
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
    client.write_text('#!/bin/sh\necho "ERROR 2002 (HY000): Can\'t connect to server" >&2\nexit 1\n')
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


class FakeBucket:
    """In-memory stand-in for the S3 client: keys to bytes."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload_stream(self, bucket: str, key: str, fileobj, low_memory: bool = False) -> None:
        data = b""
        while chunk := fileobj.read(7):
            data += chunk
        self.objects[key] = data

    def list_objects(self, bucket: str, prefix: str) -> list[str]:
        return [key for key in self.objects if key.startswith(prefix)]

    def delete_object(self, bucket: str, key: str) -> None:
        self.objects.pop(key, None)

    def download_stream(self, bucket: str, key: str, fileobj, low_memory: bool = False) -> None:
        data = self.objects[key]
        for start in range(0, len(data), 7):
            fileobj.write(data[start : start + 7])


def s3_site(tmp_path: Path) -> tuple[MagicMock, FakeBucket]:
    site = migration_site(tmp_path, "mariadb")
    site.bench.config.s3.stores_migration_backups = True
    site.bench.config.s3.bucket = "bench-bucket"
    return site, FakeBucket()


def fake_dump_cli(tmp_path: Path, output: str, exit_code: int = 0) -> str:
    cli = tmp_path / "mariadb-dump"
    cli.write_text(f"#!/bin/sh\nprintf '{output}'\nexit {exit_code}\n")
    cli.chmod(0o755)
    return str(cli)


def test_gzip_reader_produces_a_gzip_stream_in_any_read_size() -> None:
    import gzip
    import io
    import os

    from pilot.core.site.migration_backup import GzipReader

    payload = os.urandom(3 << 20)
    reader = GzipReader(io.BytesIO(payload))
    compressed = b"".join(iter(lambda: reader.read(65_537), b""))

    assert gzip.decompress(compressed) == payload


@pytest.mark.parametrize(
    ("engine", "enabled", "expected"),
    [("mariadb", True, True), ("mariadb", False, False), ("sqlite", True, False)],
)
def test_only_mariadb_dumps_go_to_the_bucket_and_only_when_enabled(
    tmp_path: Path, engine: str, enabled: bool, expected: bool
) -> None:
    site = migration_site(tmp_path, engine)
    site.bench.config.s3.stores_migration_backups = enabled

    assert SiteMigrationBackup(site).is_stored_in_s3 is expected


def test_table_dumps_stream_to_the_bucket_and_stay_off_the_disk(tmp_path: Path) -> None:
    import gzip

    site, bucket = s3_site(tmp_path)
    backup = SiteMigrationBackup(site)

    with (
        patch.object(SiteMigrationBackup, "_s3", return_value=bucket),
        patch.object(SiteMigrationBackup, "_list_tables", return_value=["tabUser"]),
        patch.object(SiteMigrationBackup, "_dump_cli", return_value=fake_dump_cli(tmp_path, "INSERT 1;")),
        patch.object(SiteMigrationBackup, "_conn_args", return_value=[]),
    ):
        assert backup.create("operation-1") == ["tabUser"]

    key = "sites/site1.localhost/migration_backups/operation-1/tabUser.sql.gz"
    assert gzip.decompress(bucket.objects[key]) == b"INSERT 1;"
    assert backup.offsite == {"bucket": "bench-bucket", "prefix": key.removesuffix("tabUser.sql.gz")}
    assert not backup._dump_path("tabUser").exists()
    assert backup.previous_tables == ["tabUser"]


def test_a_dump_that_fails_leaves_no_object_behind(tmp_path: Path) -> None:
    site, bucket = s3_site(tmp_path)
    backup = SiteMigrationBackup(site)

    with (
        patch.object(SiteMigrationBackup, "_s3", return_value=bucket),
        patch.object(SiteMigrationBackup, "_list_tables", return_value=["tabUser"]),
        patch.object(SiteMigrationBackup, "_dump_cli", return_value=fake_dump_cli(tmp_path, "partial", 2)),
        patch.object(SiteMigrationBackup, "_conn_args", return_value=[]),
        pytest.raises(BenchError, match="Failed to dump table tabUser"),
    ):
        backup.create("operation-1")

    assert bucket.objects == {}


def _offsite_backup(tmp_path: Path, tables: list[str], stored: list[str]):
    site, bucket = s3_site(tmp_path)
    backup = SiteMigrationBackup(site)
    backup.directory.mkdir()
    backup.previous_tables_path.write_text(json.dumps(tables))
    prefix = "sites/site1.localhost/migration_backups/operation-1/"
    backup.offsite_path.write_text(json.dumps({"bucket": "bench-bucket", "prefix": prefix}))
    bucket.objects = {f"{prefix}{table}.sql.gz": b"dump" for table in stored}
    return backup, bucket


def recording_client(tmp_path: Path) -> tuple[str, Path]:
    """A fake mariadb client that appends its input to a file."""
    received = tmp_path / "received.sql"
    client = tmp_path / "mariadb"
    client.write_text(f"#!/bin/sh\ncat >> {received}\n")
    client.chmod(0o755)
    return str(client), received


def test_gunzip_writer_decompresses_writes_of_any_size() -> None:
    import gzip
    import io
    import os

    from pilot.core.site.migration_backup import GunzipWriter

    payload = os.urandom(3 << 20)
    compressed = gzip.compress(payload)
    target = io.BytesIO()
    writer = GunzipWriter(target)
    for start in range(0, len(compressed), 65_537):
        writer.write(compressed[start : start + 65_537])
    writer.flush()

    assert target.getvalue() == payload


def test_restore_streams_each_dump_from_the_bucket_into_the_database(tmp_path: Path) -> None:
    import gzip

    backup, bucket = _offsite_backup(tmp_path, ["tabUser", "tabRole"], [])
    prefix = backup.offsite["prefix"]
    bucket.objects = {
        f"{prefix}tabUser.sql.gz": gzip.compress(b"INSERT user;\n"),
        f"{prefix}tabRole.sql.gz": gzip.compress(b"INSERT role;\n"),
    }
    client, received = recording_client(tmp_path)

    with (
        patch.object(SiteMigrationBackup, "_s3", return_value=bucket),
        patch.object(SiteMigrationBackup, "_cli", return_value=client),
        patch.object(SiteMigrationBackup, "_conn_args", return_value=[]),
        patch.object(SiteConfigFile, "read", return_value={"db_name": "site_db"}),
        patch.object(backup, "_list_tables", return_value=["tabUser", "tabRole"]),
    ):
        backup.restore([])

    assert received.read_bytes() == b"INSERT role;\nINSERT user;\n"
    assert sorted(path.name for path in backup.directory.iterdir()) == [
        "offsite.json",
        "previous_tables.json",
    ]


def test_a_download_that_breaks_stops_the_import(tmp_path: Path) -> None:
    import gzip

    backup, bucket = _offsite_backup(tmp_path, ["tabUser"], [])
    bucket.objects = {f"{backup.offsite['prefix']}tabUser.sql.gz": gzip.compress(b"INSERT user;\n" * 1000)}
    client, _ = recording_client(tmp_path)

    def broken_download(bucket_name, key, fileobj, low_memory=False):
        fileobj.write(bucket.objects[key][:50])
        raise S3IntegrationError("connection reset")

    bucket.download_stream = broken_download
    with (
        patch.object(SiteMigrationBackup, "_s3", return_value=bucket),
        patch.object(SiteMigrationBackup, "_cli", return_value=client),
        patch.object(SiteMigrationBackup, "_conn_args", return_value=[]),
        patch.object(SiteConfigFile, "read", return_value={"db_name": "site_db"}),
        pytest.raises(S3IntegrationError, match="connection reset"),
    ):
        backup.restore(["tabUser"])


def test_restore_refuses_before_any_import_when_an_object_is_missing(tmp_path: Path) -> None:
    backup, bucket = _offsite_backup(tmp_path, ["tabUser", "tabDocType"], ["tabUser"])

    with (
        patch.object(SiteMigrationBackup, "_s3", return_value=bucket),
        patch.object(backup, "_import_dump") as import_dump,
        pytest.raises(BenchError, match=r"tabDocType\.sql\.gz"),
    ):
        backup.restore([])

    import_dump.assert_not_called()


def test_discard_deletes_the_objects_and_the_directory(tmp_path: Path) -> None:
    backup, bucket = _offsite_backup(tmp_path, ["tabUser"], ["tabUser"])
    bucket.objects["sites/other.localhost/backups/keep.sql.gz"] = b"other"

    with patch.object(SiteMigrationBackup, "_s3", return_value=bucket):
        backup.discard()

    assert list(bucket.objects) == ["sites/other.localhost/backups/keep.sql.gz"]
    assert not backup.directory.exists()
