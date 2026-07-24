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
