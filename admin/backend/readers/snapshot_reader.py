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

    def read(self, dataset_filter: str | None = None) -> SnapshotStatus:
        from bench_cli.config.bench_config import BenchConfig
        from bench_cli.managers.volume_manager import VolumeManager

        bench_config = BenchConfig.from_file(self._bench_root / "bench.toml")
        volume_config = bench_config.volume

        if not volume_config.enabled:
            return SnapshotStatus(volume_enabled=False, snapshots_enabled=False)

        datasets = self._resolve_datasets(volume_config, dataset_filter)
        manager = VolumeManager(volume_config)
        snapshots = self._collect_snapshots(manager, datasets)
        return SnapshotStatus(
            volume_enabled=True,
            snapshots_enabled=volume_config.snapshots.enabled,
            snapshots=snapshots,
        )

    def _resolve_datasets(self, volume_config, dataset_filter: str | None) -> list[str]:
        if dataset_filter == "mariadb":
            return [volume_config.mariadb_dataset]
        if dataset_filter == "benches":
            return [volume_config.benches_dataset]
        return [volume_config.benches_dataset, volume_config.mariadb_dataset]

    def _collect_snapshots(self, manager, datasets: list[str]) -> list[SnapshotEntry]:
        snapshots = []
        for dataset in datasets:
            for snap in manager.list_snapshots(dataset):
                snapshots.append(SnapshotEntry(
                    dataset=snap.dataset,
                    tag=snap.snapshot_tag,
                    created_at=snap.created_at,
                    used_bytes=snap.used_bytes,
                ))
        return snapshots
