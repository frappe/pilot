from __future__ import annotations

import typing

if typing.TYPE_CHECKING:
    from bench_cli.core.bench import Bench
    from bench_cli.managers.mariadb_manager import MariaDBManager
    from bench_cli.managers.volume_manager import VolumeManager


class SnapshotOrchestrator:
    """Snapshot/rollback the bench's single dataset (files + database).

    Because the database lives on the same dataset, every snapshot quiesces
    MariaDB (FLUSH TABLES WITH READ LOCK) for a consistent on-disk state, and
    every rollback stops MariaDB and puts sites into maintenance mode."""

    def __init__(
        self,
        volume: VolumeManager,
        mariadb: MariaDBManager | None = None,
        bench: Bench | None = None,
    ) -> None:
        self._volume = volume
        self._mariadb = mariadb
        self._bench = bench

    @property
    def _dataset(self) -> str:
        return self._volume.config.dataset_path

    def create_snapshot(self, tag: str) -> None:
        if self._mariadb:
            with self._mariadb.snapshot_lock():
                self._volume.snapshot(self._dataset, tag)
        else:
            self._volume.snapshot(self._dataset, tag)

    def rollback_snapshot(self, tag: str) -> None:
        if self._bench:
            self._bench.set_maintenance_mode(True)
        try:
            if self._mariadb:
                self._mariadb.stop()
            try:
                self._volume.rollback_snapshot(self._dataset, tag)
            finally:
                if self._mariadb:
                    self._mariadb.start()
        finally:
            if self._bench:
                self._bench.set_maintenance_mode(False)


def get_orchestrator(bench_root):
    from bench_cli.config.bench_config import BenchConfig
    from bench_cli.core.bench import Bench
    from bench_cli.managers.mariadb_manager import MariaDBManager
    from bench_cli.managers.volume_manager import VolumeManager

    bench_config = BenchConfig.from_file(bench_root / "bench.toml")
    volume = VolumeManager(bench_config.volume)
    mariadb = MariaDBManager(bench_config.mariadb)
    bench = Bench(bench_config, bench_root)
    return SnapshotOrchestrator(volume, mariadb, bench)
