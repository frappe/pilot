import errno
import gzip
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pilot.config import SiteConfig
from pilot.core.site import Site
from pilot.core.site.backup_stream import IncompleteBackupError, SiteBackupStream
from pilot.exceptions import CommandError
from pilot.integrations.s3.backups import OffsiteBackup
from pilot.integrations.s3.base import S3, S3IntegrationError

DUMP = b"CREATE TABLE tabUser;\n" * 50_000


class FakeOffsite:
    def __init__(self, failing: str = "", failing_delete: str = "") -> None:
        self.failing = failing
        self.failing_delete = failing_delete
        self.uploaded: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def upload_stream(self, site_name, timestamp, filename, fileobj) -> None:
        data = b""
        while chunk := fileobj.read(64 * 1024):
            data += chunk
            if self.failing and filename.endswith(self.failing):
                raise S3IntegrationError(f"Failed to upload {filename}")
        self.uploaded[filename] = data

    def delete(self, site_name, timestamp, filename) -> None:
        self.deleted.append(filename)
        if self.failing_delete and filename.endswith(self.failing_delete):
            raise S3IntegrationError(f"Failed to delete {filename}")


def fake_frappe_backup(fail: bool = False):
    """Writes like Frappe: a gzip header in one session, then the dump appended in another."""

    def run(argv, **kwargs) -> None:
        paths = {flag: argv[index + 1] for index, flag in enumerate(argv) if flag.startswith("--backup-path")}
        with gzip.open(paths["--backup-path-db"], "wt") as header:
            header.write("-- begin frappe metadata\n")
        with open(paths["--backup-path-db"], "ab") as dump:
            dump.write(gzip.compress(DUMP))
        with open(paths["--backup-path-conf"], "w") as config:
            config.write("{}")
        if fail:
            raise CommandError("frappe backup failed", returncode=1)
        for flag in ("--backup-path-files", "--backup-path-private-files"):
            with open(paths[flag], "wb") as tar:
                tar.write(b"tar" * 1000)

    return run


def site(tmp_path) -> Site:
    bench = SimpleNamespace(
        path=tmp_path, sites_path=tmp_path / "sites", frappe_call=["python", "-m", "frappe"]
    )
    return Site(SiteConfig(name="site1.local", apps=[]), bench)


def test_stream_uploads_every_file_whole_and_leaves_nothing_on_disk(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("pilot.utils.run_command", fake_frappe_backup())
    offsite = FakeOffsite()

    timestamp, sizes = SiteBackupStream(site(tmp_path), offsite).run(with_files=True)

    database = f"{timestamp}-site1_local-database.sql.gz"
    assert gzip.decompress(offsite.uploaded[database]) == b"-- begin frappe metadata\n" + DUMP
    assert sorted(offsite.uploaded) == sorted(sizes)
    assert sizes == {name: len(data) for name, data in offsite.uploaded.items()}
    assert list((tmp_path / "sites" / "site1.local" / "backups").iterdir()) == []


def fail_second_holder(monkeypatch) -> None:
    """The second FIFO writer pilot opens fails as if the process ran out of file handles."""
    real_open = os.open
    calls = []

    def open_fifo(path, flags, *args, **kwargs):
        if flags == os.O_WRONLY:
            calls.append(path)
            if len(calls) == 2:
                raise OSError(errno.EMFILE, "Too many open files")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("pilot.core.site.backup_stream.os.open", open_fifo)


@pytest.mark.parametrize("failure", ["backup", "upload", "holder"])
def test_a_failed_run_fails_loudly_and_leaves_nothing_offsite(tmp_path, monkeypatch, failure) -> None:
    """A backup that fails after writing its database and config completes no upload. A failed upload drains its pipe and
    deletes every file that did upload, even when one of those deletes fails."""
    monkeypatch.setattr("pilot.utils.run_command", fake_frappe_backup(fail=failure == "backup"))
    if failure == "holder":
        fail_second_holder(monkeypatch)
    offsite = FakeOffsite(
        failing="-files.tar" if failure == "upload" else "",
        failing_delete="-site_config_backup.json" if failure == "upload" else "",
    )
    expected = {"backup": CommandError, "upload": S3IntegrationError, "holder": OSError}[failure]

    with pytest.raises(expected) as raised:
        SiteBackupStream(site(tmp_path), offsite).run(with_files=True)

    assert sorted(offsite.deleted) == sorted(offsite.uploaded)
    if failure == "backup":
        assert offsite.uploaded == {}
    if failure == "upload":
        assert len(offsite.deleted) == 2
        assert "Could not delete" in raised.value.__notes__[0]


def test_an_object_whose_index_write_fails_is_deleted() -> None:
    s3 = MagicMock()
    offsite = OffsiteBackup(s3, "bucket", "/bench")
    offsite._metadata = MagicMock(
        return_value=MagicMock(add=MagicMock(side_effect=S3IntegrationError("index")))
    )

    with pytest.raises(S3IntegrationError):
        offsite.upload_stream("site1.local", "20261022_114241", "db.sql.gz", MagicMock())

    s3.delete_object.assert_called_once_with("bucket", s3.upload_stream.call_args.args[1])


@pytest.mark.parametrize(
    ("raised", "expected"),
    [("connection", S3IntegrationError), ("reader", IncompleteBackupError)],
)
def test_stream_upload_wraps_network_errors_but_not_reader_errors(raised, expected) -> None:
    """The task records only BenchErrors; the reader's error must stay unwrapped for check_uploads."""
    from botocore.exceptions import EndpointConnectionError

    s3 = S3("key", "secret", "us-east-1", "custom", "bucket", endpoint_url="http://127.0.0.1:9")
    error = (
        EndpointConnectionError(endpoint_url="http://127.0.0.1:9")
        if raised == "connection"
        else IncompleteBackupError("cut short")
    )
    s3.client = MagicMock(upload_fileobj=MagicMock(side_effect=error))

    with pytest.raises(expected):
        s3.upload_stream("bucket", "key", MagicMock(), low_memory=True)
