from __future__ import annotations

from typing import TYPE_CHECKING

from bench_cli.managers.volume_manager import VolumeManager

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench
    from bench_cli.managers.mariadb_manager import MariaDBManager


class SnapshotOrchestrator:
    def __init__(
        self,
        volume: VolumeManager,
        mariadb: MariaDBManager | None = None,
        bench: Bench | None = None,
    ) -> None:
        self._volume = volume
        self._mariadb = mariadb
        self._bench = bench

    def create_snapshot(self, dataset: str, tag: str) -> None:
        if self._is_mariadb_dataset(dataset) and self._mariadb:
            with self._mariadb.snapshot_lock():
                self._volume.snapshot(dataset, tag)
        else:
            self._volume.snapshot(dataset, tag)

    def rollback_snapshot(self, dataset: str, tag: str) -> None:
        if self._bench:
            self._bench.set_maintenance_mode(True)
        try:
            if self._is_mariadb_dataset(dataset):
                self._rollback_mariadb(dataset, tag)
            else:
                self._volume.rollback_snapshot(dataset, tag)
        finally:
            if self._bench:
                self._bench.set_maintenance_mode(False)

    def _rollback_mariadb(self, dataset: str, tag: str) -> None:
        if self._mariadb:
            self._mariadb.stop()
        try:
            self._volume.rollback_snapshot(dataset, tag)
        finally:
            if self._mariadb:
                self._mariadb.start()

    def _is_mariadb_dataset(self, dataset: str) -> bool:
        return dataset == self._volume.config.mariadb_dataset
