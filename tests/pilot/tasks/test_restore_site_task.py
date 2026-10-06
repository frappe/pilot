from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.tasks.restore_site import RestoreSiteTask


def _task(tmp_path: Path, **source) -> tuple[RestoreSiteTask, MagicMock]:
    bench = MagicMock()
    bench.path = tmp_path
    bench.config.db_type = "mariadb"
    return RestoreSiteTask(bench=bench, bench_root=tmp_path, site="a.localhost", **source), bench


def test_a_named_run_of_another_site_is_used_as_is(tmp_path: Path) -> None:
    task, bench = _task(tmp_path, parts=["database"], source_site="b.localhost", backup_timestamp="20261004_010000")
    bench.site.return_value.backups.fetch_run.return_value = [tmp_path / "20261004_010000-b-database.sql.gz"]

    run = task.fetch(tmp_path)

    bench.site.assert_called_with("b.localhost")
    bench.site.return_value.backups.take.assert_not_called()
    assert set(run.files) == {"database"} and not run.streams


def test_without_a_run_the_other_site_is_backed_up_first(tmp_path: Path) -> None:
    task, bench = _task(tmp_path, parts=["database"], source_site="b.localhost")
    bench.site.return_value.backups.take.return_value = ("20261004_030000", [])

    task.fetch(tmp_path)

    bench.site.return_value.backups.take.assert_called_once_with(with_files=False)


def test_a_remote_restore_streams_the_chosen_parts_and_downloads_only_the_config(tmp_path: Path) -> None:
    task, _ = _task(
        tmp_path,
        parts=["database", "private"],
        remote_site="old.example.com",
        remote_password="pw",
        backup_timestamp="2",
    )
    remote = MagicMock()
    remote.get_latest_run.return_value = (
        "2",
        {
            "database": "./s/2-database.sql.gz",
            "public": "./s/2-files.tar",
            "private": "./s/2-private-files.tar",
            "config": "./s/2-site_config_backup.json",
        },
    )
    remote.download_backup.side_effect = lambda path, directory: directory / Path(path).name

    with patch("pilot.integrations.frappe_site.RemoteFrappeSite", return_value=remote):
        run = task.fetch(tmp_path)

    downloaded = [call.args[0] for call in remote.download_backup.call_args_list]
    assert downloaded == ["./s/2-site_config_backup.json"]
    assert set(run.files) == {"config"} and set(run.streams) == {"database", "private"}
    remote.open_backup.assert_not_called()  # opened only by the restore
    run.streams["private"].open()
    remote.open_backup.assert_called_once_with("./s/2-private-files.tar")
    assert run.streams["private"].name == "2-private-files.tar"


def test_a_newer_remote_backup_stops_the_restore(tmp_path: Path) -> None:
    import pytest

    from pilot.exceptions import RemoteSiteError

    task, _ = _task(
        tmp_path, parts=["public"], remote_site="old.example.com", remote_password="pw", backup_timestamp="20261004_020000"
    )
    remote = MagicMock()
    remote.get_latest_run.return_value = ("20261005_020000", {})

    with patch("pilot.integrations.frappe_site.RemoteFrappeSite", return_value=remote), pytest.raises(RemoteSiteError, match="newer backup"):
        task.fetch(tmp_path)


def test_a_frappe_cloud_restore_revokes_access_then_streams_the_chosen_parts(tmp_path: Path) -> None:
    links = {
        "database": "https://s3.example.com/s/2-database.sql.gz?X-Amz-Signature=a",
        "public": "https://s3.example.com/s/2-files.tar?X-Amz-Signature=b",
        "private": "https://s3.example.com/s/2-private-files.tar?X-Amz-Signature=c",
        "config": "https://s3.example.com/s/2-site_config_backup.json?X-Amz-Signature=d",
    }
    task, bench = _task(
        tmp_path, parts=["database", "private"], frappe_cloud_backup="backup-1", frappe_cloud_secret_urls=links
    )

    with (
        patch("pilot.integrations.frappe_cloud.download_backup", side_effect=lambda url, directory: directory / url.split("/")[-1].split("?")[0]) as download,
        patch("pilot.integrations.frappe_cloud.open_download_link") as open_link,
    ):
        run = task.fetch(tmp_path)
        bench.site.return_value.frappe_cloud.disconnect.assert_called_once()
        assert [call.args[0] for call in download.call_args_list] == [links["config"]]
        open_link.assert_not_called()  # opened only by the restore
        run.streams["database"].open()
        open_link.assert_called_once_with(links["database"])

    assert set(run.files) == {"config"} and set(run.streams) == {"database", "private"}
    assert run.streams["database"].name == "2-database.sql.gz"


def test_a_frappe_cloud_restore_names_its_first_step_with_the_backup_id(tmp_path: Path) -> None:
    task, _ = _task(tmp_path, parts=["database"], frappe_cloud_backup="backup-1", frappe_cloud_secret_urls={})

    assert task.fetch_label == "Prepare the Frappe Cloud backup (backup-1)"


def test_a_remote_restore_of_only_the_site_config_downloads_only_the_config(tmp_path: Path) -> None:
    task, _ = _task(tmp_path, parts=["config"], remote_site="old.example.com", remote_password="pw", backup_timestamp="2")
    remote = MagicMock()
    remote.get_latest_run.return_value = ("2", {"database": "./s/2-database.sql.gz", "config": "./s/2-site_config_backup.json"})
    remote.download_backup.side_effect = lambda path, directory: directory / Path(path).name

    with patch("pilot.integrations.frappe_site.RemoteFrappeSite", return_value=remote):
        run = task.fetch(tmp_path)

    assert [call.args[0] for call in remote.download_backup.call_args_list] == ["./s/2-site_config_backup.json"]
    assert set(run.files) == {"config"} and not run.streams
