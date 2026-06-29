from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class SnapshotEntry:
    dataset: str
    tag: str
    created_at: datetime
    used_bytes: int


@dataclass
class SnapshotStatus:
    volume_enabled: bool
    snapshots_enabled: bool
    snapshots: list[SnapshotEntry] = field(default_factory=list)


class SnapshotReader:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def read(self) -> SnapshotStatus:
        from pilot.config.toml_store import BenchTomlStore
        from pilot.managers.volume_manager import VolumeManager
        from pilot.platform import is_linux

        bench_config = BenchTomlStore.for_bench(self._bench_root).read()
        volume_config = bench_config.volume

        if not is_linux():
            return SnapshotStatus(volume_enabled=False, snapshots_enabled=False)

        manager = VolumeManager(volume_config)
        snapshots = self._collect_snapshots(manager, volume_config.dataset_path)
        return SnapshotStatus(
            volume_enabled=True,
            snapshots_enabled=True,
            snapshots=snapshots,
        )

    def _collect_snapshots(self, manager, dataset: str) -> list[SnapshotEntry]:
        return [
            SnapshotEntry(
                dataset=snap.dataset,
                tag=snap.snapshot_tag,
                created_at=snap.created_at,
                used_bytes=snap.used_bytes,
            )
            for snap in manager.list_snapshots(dataset)
        ]
