from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DatasetInfo:
    name: str
    used_bytes: int
    available_bytes: int
    quota_bytes: int
    reservation_bytes: int


@dataclass
class VolumeInfo:
    enabled: bool
    pool: str = ""
    pool_health: str = ""
    datasets: list[DatasetInfo] = field(default_factory=list)


class VolumeReader:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def read(self) -> VolumeInfo:
        from bench_cli.config.bench_config import BenchConfig

        config = BenchConfig.from_file(self._bench_root / "bench.toml").volume
        if not config.enabled:
            return VolumeInfo(enabled=False)
        return VolumeInfo(
            enabled=True,
            pool=config.pool,
            pool_health=self._pool_health(config.pool),
            datasets=[
                self._read_dataset(config.benches_dataset),
                self._read_dataset(config.mariadb_dataset),
            ],
        )

    def _pool_health(self, pool: str) -> str:
        result = subprocess.run(["zpool", "list", "-H", "-o", "health", pool], capture_output=True)
        if result.returncode == 0:
            return result.stdout.decode().strip()
        return "unknown"

    def _read_dataset(self, dataset: str) -> DatasetInfo:
        result = subprocess.run(
            ["zfs", "list", "-H", "-p", "-o", "name,used,avail,quota,reservation", dataset],
            capture_output=True,
        )
        if result.returncode != 0:
            return DatasetInfo(name=dataset, used_bytes=0, available_bytes=0, quota_bytes=0, reservation_bytes=0)
        name, used, avail, quota, reservation = result.stdout.decode().strip().split("\t")
        return DatasetInfo(
            name=name,
            used_bytes=int(used),
            available_bytes=int(avail),
            quota_bytes=int(quota),
            reservation_bytes=int(reservation),
        )
