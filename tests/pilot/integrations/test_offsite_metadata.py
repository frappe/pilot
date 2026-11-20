import threading
import time

from pilot.integrations.s3.backups import BackupKeys, Metadata


class _FakeS3:
    """In-memory S3 whose read/write straddle a delay, widening the
    read-modify-write window so an unlocked writer would lose runs."""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.active = 0
        self.max_active = 0
        self._guard = threading.Lock()

    def has_object(self, bucket: str, key: str) -> bool:
        return key in self.objects

    def read_json(self, bucket: str, key: str) -> dict:
        time.sleep(0.01)
        return {ts: dict(run) for ts, run in self.objects.get(key, {}).items()}

    def write_json(self, bucket: str, key: str, data: dict) -> None:
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.01)
        self.objects[key] = data
        with self._guard:
            self.active -= 1

    def list_objects(self, bucket: str, prefix: str) -> list[str]:
        return [key for key in self.objects if key.startswith(prefix)]

    def delete_object(self, bucket: str, key: str) -> None:
        self.objects.pop(key, None)


def _run_concurrently(target, count: int) -> None:
    ready = threading.Barrier(count)

    def worker(index: int) -> None:
        ready.wait()
        target(index)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def test_concurrent_adds_keep_every_run(tmp_path) -> None:
    s3 = _FakeS3()
    metadata = Metadata(s3, "bucket", BackupKeys("site"), tmp_path / ".backup-metadata")
    timestamps = [f"20260701_0000{i:02d}" for i in range(8)]

    _run_concurrently(
        lambda i: metadata.add(timestamps[i], f"{timestamps[i]}-database.sql.gz"), len(timestamps)
    )

    stored = s3.objects[BackupKeys("site").get_month_key(timestamps[0])]
    assert set(stored) == set(timestamps)
    assert s3.max_active == 1


def test_concurrent_add_and_remove_do_not_corrupt(tmp_path) -> None:
    s3 = _FakeS3()
    keys = BackupKeys("site")
    metadata = Metadata(s3, "bucket", keys, tmp_path / ".backup-metadata")
    timestamps = [f"20260701_0000{i:02d}" for i in range(6)]
    for ts in timestamps:
        metadata.add(ts, f"{ts}-database.sql.gz")

    _run_concurrently(lambda i: metadata.remove(timestamps[i], f"{timestamps[i]}-database.sql.gz"), 3)

    stored = s3.objects[keys.get_month_key(timestamps[0])]
    assert set(stored) == set(timestamps[3:])
    assert s3.max_active == 1


def test_uploaded_file_sizes_list_only_the_run(tmp_path) -> None:
    from unittest.mock import Mock

    from pilot.integrations.s3.backups import OffsiteBackup

    s3 = Mock()
    s3.get_object_sizes.return_value = {
        "sites/site/backups/20260701/020000/20260701_020000-site-database.sql.gz": 2048,
        "sites/site/backups/20260701/020000/20260701_020000-site-files.tar": 10,
    }

    sizes = OffsiteBackup(s3, "bucket", tmp_path).get_file_sizes("site", "20260701_020000")

    s3.get_object_sizes.assert_called_once_with("bucket", "sites/site/backups/20260701/020000/")
    assert sizes == {"20260701_020000-site-database.sql.gz": 2048, "20260701_020000-site-files.tar": 10}


INDEX = {
    "files_index": [{"name": "logo.png", "file_url": "/files/logo.png", "size": 10}],
    "private_files_index": [
        {"name": "a.pdf", "file_url": "/private/files/bl0b/a.pdf", "size": 20, "file_blob": "bl0b"}
    ],
}


def _offsite(tmp_path):
    from pilot.integrations.s3.backups import OffsiteBackup

    s3 = _FakeS3()
    return s3, OffsiteBackup(s3, "bucket", tmp_path)


def test_files_index_key_uses_the_backup_name() -> None:
    """The index sits in backups_metadata, named after the run like Frappe names its backup files."""
    key = BackupKeys("frappe-bench-3.localhost").get_files_index_key("20261117_162033")

    assert key == (
        "sites/frappe-bench-3.localhost/backups_metadata/20261117_162033-frappe-bench-3_localhost-files-index.json"
    )


def test_files_index_is_a_separate_object_and_the_month_is_unchanged(tmp_path) -> None:
    """The month index keeps only file names, so its callers never see the index lists."""
    s3, offsite = _offsite(tmp_path)
    keys = BackupKeys("site")
    offsite._metadata(keys).add("20260701_020000", "20260701_020000-site-files.tar")
    month = dict(s3.objects[keys.get_month_key("20260701_020000")])

    offsite.set_files_index("site", "20260701_020000", INDEX)

    assert s3.objects[keys.get_month_key("20260701_020000")] == month
    assert s3.objects[keys.get_files_index_key("20260701_020000")] == INDEX


def test_listing_runs_ignores_files_index_objects(tmp_path) -> None:
    """Index objects share the month prefix; read as months they would show up as runs."""
    _, offsite = _offsite(tmp_path)
    offsite._metadata(BackupKeys("site")).add("20260701_020000", "20260701_020000-site-files.tar")
    offsite.set_files_index("site", "20260701_020000", INDEX)

    runs = offsite.list_backups("site")

    assert runs == {"20260701_020000": {"files": "20260701_020000-site-files.tar"}}


def test_index_is_deleted_with_the_last_file_of_its_run(tmp_path) -> None:
    """Retention and manual deletes go file by file; the index goes when the run is gone."""
    s3, offsite = _offsite(tmp_path)
    keys = BackupKeys("site")
    metadata = offsite._metadata(keys)
    metadata.add("20260701_020000", "20260701_020000-site-files.tar")
    metadata.add("20260701_020000", "20260701_020000-site-database.sql.gz")
    offsite.set_files_index("site", "20260701_020000", INDEX)

    offsite.delete("site", "20260701_020000", "20260701_020000-site-files.tar")
    assert keys.get_files_index_key("20260701_020000") in s3.objects

    offsite.delete("site", "20260701_020000", "20260701_020000-site-database.sql.gz")
    assert keys.get_files_index_key("20260701_020000") not in s3.objects


def test_deleting_a_run_without_an_index_does_not_fail(tmp_path) -> None:
    """Runs from before the index existed still delete cleanly."""
    s3, offsite = _offsite(tmp_path)
    keys = BackupKeys("site")
    offsite._metadata(keys).add("20260701_020000", "20260701_020000-site-files.tar")

    offsite.delete("site", "20260701_020000", "20260701_020000-site-files.tar")

    assert s3.objects[keys.get_month_key("20260701_020000")] == {}


def test_failed_index_delete_keeps_the_run_listed(tmp_path) -> None:
    """A run that drops out of the listing first would leave its index unreachable and its blobs pinned."""
    import pytest

    s3, offsite = _offsite(tmp_path)
    keys = BackupKeys("site")
    offsite._metadata(keys).add("20260701_020000", "20260701_020000-site-files.tar")
    offsite.set_files_index("site", "20260701_020000", INDEX)

    def delete_object(bucket: str, key: str) -> None:
        if key == keys.get_files_index_key("20260701_020000"):
            raise OSError("S3 unavailable")
        s3.objects.pop(key, None)

    s3.delete_object = delete_object
    with pytest.raises(OSError):
        offsite.delete("site", "20260701_020000", "20260701_020000-site-files.tar")

    assert "20260701_020000" in offsite.list_backups("site")
