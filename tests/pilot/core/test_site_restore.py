from __future__ import annotations

import gzip
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.site.restore import BackupRun, BackupStream, SiteRestore, backup_part
from pilot.exceptions import BenchError


def _frappe_archive(tmp_path: Path, site: str, part: str) -> Path:
    """Built the way Frappe's backup builds it: `tar cf` of ./<site>/<part>/files from sites/."""
    sites = tmp_path / "source-sites"
    (sites / site / part / "files").mkdir(parents=True)
    (sites / site / part / "files" / "logo.png").write_text("image")
    archive = tmp_path / f"20261004_010000-{site}-{'private-' if part == 'private' else ''}files.tar"
    subprocess.run(["tar", "-cf", str(archive), f"./{site}/{part}/files"], cwd=sites, check=True)
    return archive


def _site(tmp_path: Path) -> MagicMock:
    site = MagicMock()
    site.path = tmp_path / "target"
    site.path.mkdir()
    site.config.name = "target.localhost"
    return site


def test_backup_parts_are_told_apart_by_name() -> None:
    names = {
        "x-database.sql.gz": "database",
        "x-files.tar": "public",
        "x-private-files.tgz": "private",
        "x-site_config_backup.json": "config",
        "notes.txt": None,
    }
    assert {name: backup_part(name) for name in names} == names


def test_files_from_another_site_land_in_this_site(tmp_path: Path) -> None:
    site = _site(tmp_path)
    run = BackupRun.from_paths([_frappe_archive(tmp_path, "source.localhost", "public")])

    SiteRestore(site).restore(run, ["public"], on_progress=lambda message: None)

    assert (site.path / "public" / "files" / "logo.png").read_text() == "image"
    site.restore.assert_not_called()  # files only: the database is left alone
    site.migrate.assert_called_once()
    assert site.set_maintenance_mode.call_args_list[-1].args == (False,)


@pytest.mark.parametrize("is_compressed", [False, True])
def test_a_streamed_archive_lands_in_this_site_without_a_copy_on_disk(tmp_path: Path, is_compressed: bool) -> None:
    site = _site(tmp_path)
    archive = _frappe_archive(tmp_path, "source.localhost", "public")
    data = gzip.compress(archive.read_bytes()) if is_compressed else archive.read_bytes()
    name = archive.name.replace(".tar", ".tgz") if is_compressed else archive.name
    run = BackupRun(streams={"public": BackupStream(name, lambda: io.BytesIO(data))})

    SiteRestore(site).restore(run, ["public"], on_progress=lambda message: None)

    assert (site.path / "public" / "files" / "logo.png").read_text() == "image"


def test_a_streamed_extraction_with_much_error_output_does_not_hang(tmp_path: Path, monkeypatch) -> None:
    """tar that writes more to stderr than a pipe holds must still read the whole archive."""
    import threading

    real_popen = subprocess.Popen
    noisy_tar = ["sh", "-c", "head -c 300000 /dev/zero | tr '\\0' e >&2; cat > /dev/null; exit 2"]
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **options: real_popen(noisy_tar, **options))
    stream = BackupStream("x-files.tar", lambda: io.BytesIO(b"x" * 1_000_000))
    errors: list[BaseException] = []

    def extract() -> None:
        try:
            SiteRestore(_site(tmp_path)).extract_files_stream(stream, "public")
        except BenchError as error:
            errors.append(error)

    worker = threading.Thread(target=extract, daemon=True)
    worker.start()
    worker.join(timeout=20)

    assert not worker.is_alive()
    assert len(errors) == 1 and "eee" in str(errors[0])
    assert len(str(errors[0])) < 5000  # only the end of the error output


def test_a_failed_file_stream_stops_the_restore_before_the_database(tmp_path: Path) -> None:
    site = _site(tmp_path)
    archive = _frappe_archive(tmp_path, "source.localhost", "public")

    def broken_download() -> io.BytesIO:
        raise OSError("connection reset")

    run = BackupRun(files={"database": archive}, streams={"public": BackupStream(archive.name, broken_download)})

    with pytest.raises(OSError, match="connection reset"):
        SiteRestore(site).restore(run, ["public", "database"], lambda message: None)

    site.restore.assert_not_called()


def _source_config(tmp_path: Path) -> Path:
    config = tmp_path / "20261004_010000-source-site_config_backup.json"
    source_config = {
        "encryption_key": "source-key",
        "max_file_size": 50,
        "db_name": "_source",
        "db_password": "source-password",
        "redis_cache": "redis://source",
        "host_name": "https://source.example.com",
        "pilot_auth_token": "source-token",
        "maintenance_mode": 1,
    }
    config.write_text(json.dumps(source_config))
    return config


def test_a_restored_database_brings_its_apps_and_only_the_encryption_key_of_the_config(tmp_path: Path) -> None:
    site = _site(tmp_path)
    config = _source_config(tmp_path)
    database = tmp_path / "20261004_010000-source-database.sql.gz"
    database.write_bytes(b"")

    with patch("pilot.core.site.config.query_installed_apps_via_db", return_value=["frappe", "payments"]):
        SiteRestore(site).restore(BackupRun.from_paths([database, config]), ["database"], lambda message: None)

    site.restore.assert_called_once_with(str(database))
    site.set_config_values.assert_called_once_with(
        {"encryption_key": "source-key", "installed_apps": ["frappe", "payments"]}
    )


def test_a_restored_site_config_keeps_the_keys_that_belong_to_this_site(tmp_path: Path) -> None:
    site = _site(tmp_path)
    database = tmp_path / "20261004_010000-source-database.sql.gz"
    database.write_bytes(b"")
    run = BackupRun.from_paths([database, _source_config(tmp_path)])

    with patch("pilot.core.site.config.query_installed_apps_via_db", return_value=None):
        SiteRestore(site).restore(run, ["database", "config"], lambda message: None)

    assert site.set_config_values.call_args_list[-1].args == ({"encryption_key": "source-key", "max_file_size": 50},)


def test_skipping_failing_patches_reaches_the_migration(tmp_path: Path) -> None:
    site = _site(tmp_path)

    SiteRestore(site).restore(
        BackupRun.from_paths([_source_config(tmp_path)]), ["config"], lambda message: None, skip_failing_patches=True
    )

    site.migrate.assert_called_once_with(skip_failing=True)


def test_a_failed_restore_keeps_the_site_in_maintenance(tmp_path: Path) -> None:
    site = _site(tmp_path)
    site.migrate.side_effect = BenchError("patch failed")
    messages: list[str] = []
    run = BackupRun.from_paths([_frappe_archive(tmp_path, "source.localhost", "public")])

    with pytest.raises(BenchError, match="patch failed"):
        SiteRestore(site).restore(run, ["public"], messages.append)

    assert site.set_maintenance_mode.call_args_list[-1].args == (True,)
    assert "maintenance mode" in messages[-1]


def test_a_missing_part_is_refused_before_anything_changes(tmp_path: Path) -> None:
    site = _site(tmp_path)

    with pytest.raises(BenchError, match="no private file"):
        SiteRestore(site).restore(BackupRun(), ["private"], lambda message: None)

    site.set_maintenance_mode.assert_not_called()


def test_a_dump_streams_into_the_client(tmp_path: Path, monkeypatch) -> None:
    from pilot.managers.database import mariadb as module

    output = tmp_path / "imported.sql"
    manager = module.MariaDBManager.__new__(module.MariaDBManager)
    manager.config = SimpleNamespace(root_password="secret")
    monkeypatch.setattr(module.MariaDBManager, "_client_command", lambda self: ["sh", "-c", f"cat > {output}", "x"])

    manager.import_sql("site_db", gzip.GzipFile(fileobj=io.BytesIO(gzip.compress(b"INSERT 1;\n" * 1000))))

    assert output.read_bytes() == b"INSERT 1;\n" * 1000


def test_an_archive_cannot_write_outside_the_files_directory(tmp_path: Path) -> None:
    site = _site(tmp_path)
    (site.path / "site_config.json").write_text('{"db_name": "real"}')
    sites = tmp_path / "evil-sites"
    (sites / "x" / "public" / "files").mkdir(parents=True)
    (sites / "x" / "site_config.json").write_text('{"db_name": "attacker"}')
    archive = tmp_path / "20261004_010000-x-files.tar"
    subprocess.run(["tar", "-cf", str(archive), "./x/public/files", "./x/site_config.json"], cwd=sites, check=True)

    SiteRestore(site).extract_files(archive, "public")

    assert json.loads((site.path / "site_config.json").read_text()) == {"db_name": "real"}


def test_a_config_restored_without_its_database_keeps_this_sites_encryption_key(tmp_path: Path) -> None:
    site = _site(tmp_path)

    SiteRestore(site).restore(BackupRun.from_paths([_source_config(tmp_path)]), ["config"], lambda message: None)

    site.set_config_values.assert_called_once_with({"max_file_size": 50})
