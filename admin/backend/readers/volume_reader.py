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
        from pilot.config.bench_config import BenchConfig
        from pilot.platform import is_linux

        config = BenchConfig.from_file(self._bench_root / "bench.toml").volume
        # A bench can opt out of ZFS (shared-DB benches set volume.enabled =
        # false). Since pools are shared across benches, never infer "enabled"
        # from the pool merely existing — honour the bench's own config first.
        if not config.enabled:
            return VolumeInfo(enabled=False, pool=config.pool)
        # "enabled" then reflects live state — whether this bench's pool actually
        # exists yet (False on macOS and before bench init).
        health = self._pool_health(config.pool) if is_linux() else "unknown"
        if health == "unknown":
            return VolumeInfo(enabled=False, pool=config.pool)
        return VolumeInfo(
            enabled=True,
            pool=config.pool,
            pool_health=health,
            datasets=[
                self._read_dataset(config.dataset_path),
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
