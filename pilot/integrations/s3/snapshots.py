"""Offsite ZFS snapshot storage: streams a bench's snapshots to/from S3."""

import subprocess
from collections.abc import Iterator
from dataclasses import dataclass

from pilot.config.s3_config import S3Config
from pilot.integrations.s3.base import S3, S3IntegrationError
from pilot.platform import _privileged


@dataclass(frozen=True)
class SnapshotKeys:
    """Every S3 key the snapshot feature touches, built in one place.

    Fixed layout::

        snapshots/<bench>/snapshots/<date>/<time>                 snapshot stream
        snapshots/<bench>/snapshots_metadata/<year>-<month>.json  monthly run index
    """

    bench_name: str

    def file(self, timestamp: str) -> str:
        date, time = timestamp.split("-")
        return f"snapshots/{self.bench_name}/snapshots/{date}/{time}"

    def month(self, timestamp: str) -> str:
        date = timestamp.split("-")[0]
        return f"snapshots/{self.bench_name}/snapshots_metadata/{date[:4]}-{date[4:6]}.json"

    @property
    def month_prefix(self) -> str:
        return f"snapshots/{self.bench_name}/snapshots_metadata/"


class Metadata:
    """Monthly index of one bench's offsite ZFS snapshots.

    Each monthly file groups snapshot runs by timestamp::

        {
          "20260702-174545": {"key": "snapshots/my-bench/snapshots/20260702/174545"}
        }
    """

    def __init__(self, s3: S3, bucket: str, keys: SnapshotKeys):
        self.s3 = s3
        self.bucket = bucket
        self.keys = keys

    def add(self, timestamp: str, remote_key: str) -> None:
        key = self.keys.month(timestamp)
        runs = self._read_month(key)
        runs[timestamp] = {"key": remote_key}
        self.s3.write_json(self.bucket, key, runs)

    def remove(self, timestamp: str) -> None:
        key = self.keys.month(timestamp)
        runs = self._read_month(key)
        if runs.pop(timestamp, None) is None:
            return
        self.s3.write_json(self.bucket, key, runs)

    def iter_runs(self) -> Iterator[tuple[str, dict[str, str]]]:
        """(timestamp, entry) pairs across every monthly file, newest first.
        Fetches one month at a time so a caller that only needs the most recent
        runs can stop early instead of paying for the whole history."""
        month_keys = self.s3.list_objects(self.bucket, prefix=self.keys.month_prefix)
        for key in sorted(month_keys, reverse=True):
            runs = self.s3.read_json(self.bucket, key)
            for timestamp in sorted(runs, reverse=True):
                yield timestamp, runs[timestamp]

    def _read_month(self, key: str) -> dict[str, dict[str, str]]:
        if not self.s3.object_exists(self.bucket, key):
            return {}
        return self.s3.read_json(self.bucket, key)


class OffsiteSnapshot:
    """Streams a bench's ZFS snapshots (files + database, atomic — see
    `SnapshotOrchestrator`) to and from one bench's bucket.

    Wraps a configured ``S3`` client (composition, not inheritance: this class
    is not itself an S3 client), with all key naming delegated to
    ``SnapshotKeys`` and run bookkeeping to ``Metadata``.
    """

    def __init__(self, s3: S3, bucket: str) -> None:
        self.s3 = s3
        self.bucket = bucket

    @classmethod
    def from_config(cls, config: S3Config) -> "OffsiteSnapshot":
        client = S3.from_config(config)
        return cls(client, config.bucket)

    def upload(self, bench_name: str, timestamp: str, dataset: str) -> None:
        """Streams `zfs send <dataset>@<timestamp>` straight into S3 — the
        snapshot data never touches local disk or is buffered in memory."""
        keys = SnapshotKeys(bench_name)
        remote_key = keys.file(timestamp)
        self._send(f"{dataset}@{timestamp}", remote_key)
        self._metadata(keys).add(timestamp, remote_key)

    def download(self, bench_name: str, timestamp: str, dataset: str) -> str:
        """Streams the S3 snapshot object straight into `zfs receive`, into a
        fresh `<dataset>-restored-<timestamp>` dataset rather than the live
        one: a full-stream `zfs receive` onto an existing dataset needs -F,
        which rolls the destination back to match the stream — on the live
        dataset that would destroy anything written since. Promoting the
        restored dataset to live is a separate, explicit operation (see
        `SnapshotOrchestrator.restore_downloaded_snapshot`). Returns the
        restored dataset path."""
        keys = SnapshotKeys(bench_name)
        restore_dataset = f"{dataset}-restored-{timestamp}"
        self._receive(keys.file(timestamp), restore_dataset)
        return restore_dataset

    def delete(self, bench_name: str, timestamp: str) -> None:
        keys = SnapshotKeys(bench_name)
        self.s3.delete_object(self.bucket, keys.file(timestamp))
        self._metadata(keys).remove(timestamp)

    def list_snapshots(self, bench_name: str, limit: int | None = None) -> dict[str, dict[str, str]]:
        """Offsite snapshot runs for a bench, newest first, keyed by timestamp.
        Stops reading monthly metadata files as soon as `limit` runs are
        collected, instead of fetching a bench's entire snapshot history."""
        runs: dict[str, dict[str, str]] = {}
        for timestamp, entry in self._metadata(SnapshotKeys(bench_name)).iter_runs():
            runs[timestamp] = entry
            if limit is not None and len(runs) >= limit:
                break
        return runs

    def _send(self, dataset_snapshot: str, remote_key: str) -> None:
        argv = _privileged(["zfs", "send", dataset_snapshot])
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            try:
                self.s3.upload_stream(self.bucket, remote_key, proc.stdout)
            finally:
                proc.stdout.close()
            stderr = proc.stderr.read()
            proc.wait()
            if proc.returncode != 0:
                raise S3IntegrationError(f"zfs send failed: {stderr.decode().strip()}")

    def _receive(self, remote_key: str, restore_dataset: str) -> None:
        # No -F: restore_dataset is a fresh, uniquely-named dataset that never
        # already exists, so a plain full-stream receive is always safe.
        argv = _privileged(["zfs", "receive", "-u", restore_dataset])
        with subprocess.Popen(argv, stdin=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            try:
                self.s3.download_stream(self.bucket, remote_key, proc.stdin)
            finally:
                proc.stdin.close()
            stderr = proc.stderr.read()
            proc.wait()
            if proc.returncode != 0:
                raise S3IntegrationError(f"zfs receive failed: {stderr.decode().strip()}")

    def _metadata(self, keys: SnapshotKeys) -> Metadata:
        return Metadata(self.s3, self.bucket, keys)
