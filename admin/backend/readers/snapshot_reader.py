from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pilot.integrations.s3.snapshots import OffsiteSnapshot


@dataclass
class SnapshotEntry:
    dataset: str
    tag: str
    created_at: datetime
    used_bytes: int
    is_offsite: bool = False
    # False only for a remote-only snapshot (already offloaded, local copy
    # destroyed) — `zfs rollback` needs a local snapshot, so these can't be
    # restored until downloaded back.
    is_local: bool = True
    # True while an offsite-snapshot task for this tag is still running — a
    # successful upload destroys the local copy, so a rollback started now
    # could have its snapshot vanish out from under it.
    is_uploading: bool = False
    # True when a remote-only snapshot has been fetched into
    # `<dataset>-restored-<tag>` (see OffsiteSnapshot.download) — a real
    # local ZFS snapshot, distinct from the live dataset's own snapshots.
    is_downloaded: bool = False


@dataclass
class SnapshotStatus:
    volume_enabled: bool
    snapshots: list[SnapshotEntry] = field(default_factory=list)


class SnapshotReader:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def read(self, limit: int | None = None) -> SnapshotStatus:
        """Snapshots newest first: local runs, overlaid with offsite runs from
        S3. When `limit` is given, only that many offsite runs are fetched (the
        monthly metadata files are read lazily), so a paginated caller never
        pays for a bench's full history."""
        from pilot.config.toml_store import BenchTomlStore
        from pilot.managers.volume_manager import VolumeManager
        from pilot.platform import is_linux

        self._bench_config = BenchTomlStore.for_bench(self._bench_root).read()
        volume_config = self._bench_config.volume

        if not is_linux():
            return SnapshotStatus(volume_enabled=False)

        manager = VolumeManager(volume_config)
        dataset = volume_config.dataset_path
        entries = {entry.tag: entry for entry in self._read_local_snapshots(manager, dataset)}
        self._overlay_remote_snapshots(entries, dataset, limit)
        self._overlay_downloaded_snapshots(entries, manager, dataset)

        for tag in self._uploading_tags():
            if entry := entries.get(tag):
                entry.is_uploading = True

        ordered = sorted(entries.values(), key=lambda entry: entry.tag, reverse=True)
        return SnapshotStatus(
            volume_enabled=True,
            snapshots=ordered[:limit] if limit is not None else ordered,
        )

    def _read_local_snapshots(self, manager, dataset: str) -> list[SnapshotEntry]:
        return [
            SnapshotEntry(dataset=snap.dataset, tag=snap.snapshot_tag, created_at=snap.created_at, used_bytes=snap.used_bytes)
            for snap in manager.list_snapshots(dataset)
        ]

    def _overlay_remote_snapshots(self, entries: dict[str, SnapshotEntry], dataset: str, limit: int | None) -> None:
        """Marks snapshots that exist offsite and adds remote-only ones. A
        snapshot still present locally is left untouched — the local copy is
        authoritative (it has a real, known size)."""
        if not self._bench_config.s3.is_configured:
            return

        offsite_snapshot = OffsiteSnapshot.from_config(self._bench_config.s3)
        for tag in offsite_snapshot.list_snapshots(self._bench_config.name, limit=limit):
            entry = entries.setdefault(
                tag, SnapshotEntry(dataset=dataset, tag=tag, created_at=self._parse_tag(tag), used_bytes=0, is_local=False)
            )
            entry.is_offsite = True

    def _overlay_downloaded_snapshots(self, entries: dict[str, SnapshotEntry], manager, dataset: str) -> None:
        """Marks tags fetched into `<dataset>-restored-<tag>` — a real local
        ZFS snapshot, separate from the live dataset's own history."""
        prefix = f"{dataset}-restored-"
        for name in manager.list_dataset_names(prefix):
            tag = name[len(prefix):]
            entry = entries.setdefault(
                tag, SnapshotEntry(dataset=dataset, tag=tag, created_at=self._parse_tag(tag), used_bytes=0, is_local=False)
            )
            entry.is_downloaded = True

    def _uploading_tags(self) -> set[str]:
        """Tags with an offsite-snapshot task still running. Reading the task
        registry is a handful of small local file reads."""
        from admin.backend.tasks.manager.task_reader import TaskReader

        try:
            tasks = TaskReader(self._bench_root).list_tasks()
        except Exception:
            return set()
        return {
            task.args["tag"]
            for task in tasks
            if task.status == "running" and task.command == "offsite-snapshot" and task.args.get("tag")
        }

    def _parse_tag(self, tag: str) -> datetime:
        try:
            return datetime.strptime(tag, "%Y%m%d-%H%M%S")
        except ValueError:
            return datetime.now()
