from __future__ import annotations

from datetime import datetime
from pathlib import Path

from bench_cli.config.volume_config import VolumeConfig
from bench_cli.exceptions import BenchError, CommandError
from bench_cli.platform import is_linux
from bench_cli.utils import run_command
from bench_cli.managers.volume_manager import VolumeManager


def _require_enabled(config: VolumeConfig):
    if not config.enabled:
        raise BenchError("Volume management is disabled. Set volume.enabled = true in bench.toml.")


def _stop_mariadb() -> None:
    try:
        run_command(["sudo", "systemctl", "stop", "mariadb"])
    except CommandError:
        pass


def _start_mariadb() -> None:
    try:
        run_command(["sudo", "systemctl", "start", "mariadb"])
    except CommandError as e:
        print(f"Warning: failed to restart MariaDB service: {e}")


class VolumeSetupCommand:
    def __init__(self, config: VolumeConfig, bench_path: Path) -> None:
        self.config = config
        self.bench_path = bench_path

    def setup_mariadb(self, manager: VolumeManager):
        data_dir = Path(self.config.mariadb.data_dir)
        has_data = data_dir.exists() and any(data_dir.iterdir())

        print(f"Creating ZFS pool '{self.config.pool}' and mariadb dataset...")
        manager.setup()

        if has_data:
            print(f"Existing data found at {data_dir}, stopping MariaDB for migration...")
            _stop_mariadb()
            manager.migrate_data(self.config.mariadb_dataset, data_dir)

        manager.set_mountpoint(self.config.mariadb_dataset, data_dir)

        if has_data:
            _start_mariadb()

    def setup_bench(self, manager: VolumeManager):
        data_dir = self.bench_path.parent
        manager.migrate_data(self.config.benches_dataset, data_dir)
        manager.set_mountpoint(self.config.benches_dataset, data_dir)

    def run(self) -> None:
        if not is_linux():
            raise BenchError("Volume management requires Linux (ZFS is not supported on macOS).")

        _require_enabled(self.config)

        manager = VolumeManager(self.config)
        self.setup_mariadb(manager)
        self.setup_bench(manager)
        print("Volume setup complete.")


class VolumeStatusCommand:
    def __init__(self, config: VolumeConfig) -> None:
        self.config = config

    def run(self) -> None:
        if not self.config.enabled:
            print("Volume management is disabled (volume.enabled = false).")
            return
        self._print_pool()
        self._print_dataset(self.config.benches_dataset)
        self._print_dataset(self.config.mariadb_dataset)

    def _print_pool(self) -> None:
        try:
            result = run_command(["zpool", "list", "-H", "-o", "name,health,size,free", self.config.pool])
        except CommandError:
            print(f"Pool       {self.config.pool:<20} not found")
            return
        name, health, size, free = result.stdout.decode().strip().split("\t")
        print(f"Pool       {name:<20} {health}  size={size}  free={free}")

    def _print_dataset(self, dataset: str) -> None:
        try:
            result = run_command(["zfs", "list", "-H", "-o", "name,quota,reservation,used,avail", dataset])
        except CommandError:
            print(f"Dataset    {dataset:<30} not found")
            return
        name, quota, reservation, used, avail = result.stdout.decode().strip().split("\t")
        print(f"Dataset    {name:<30} quota={quota}  reservation={reservation}  used={used}  avail={avail}")


class VolumeSnapshotCommand:
    def __init__(self, config: VolumeConfig, dataset_name: str | None) -> None:
        self.config = config
        self.dataset_name = dataset_name

    def run(self) -> None:
        _require_enabled(self.config)
        if not self.config.snapshots.enabled:
            raise BenchError("Snapshots are disabled. Set volume.snapshots.enabled = true in bench.toml.")
        from bench_cli.managers.volume_manager import VolumeManager

        manager = VolumeManager(self.config)
        tag = datetime.now().strftime("%Y%m%d-%H%M%S")
        for dataset in self._target_datasets():
            manager.snapshot(dataset, tag)
            print(f"Snapshot created: {dataset}@{tag}")

    def _target_datasets(self) -> list[str]:
        if self.dataset_name == "benches":
            return [self.config.benches_dataset]
        if self.dataset_name == "mariadb":
            return [self.config.mariadb_dataset]
        return [self.config.benches_dataset, self.config.mariadb_dataset]


class VolumeListSnapshotsCommand:
    def __init__(self, config: VolumeConfig, dataset_name: str | None) -> None:
        self.config = config
        self.dataset_name = dataset_name

    def run(self) -> None:
        _require_enabled(self.config)
        from bench_cli.managers.volume_manager import VolumeManager

        manager = VolumeManager(self.config)
        for dataset in self._target_datasets():
            snapshots = manager.list_snapshots(dataset)
            print(f"Dataset: {dataset}")
            if not snapshots:
                print("  (no snapshots)")
                continue
            for snap in snapshots:
                used_mb = snap.used_bytes // (1024 * 1024)
                ts = snap.created_at.strftime("%Y-%m-%d %H:%M:%S")
                print(f"  {snap.snapshot_tag:<30} created: {ts}  used: {used_mb}M")

    def _target_datasets(self) -> list[str]:
        if self.dataset_name == "benches":
            return [self.config.benches_dataset]
        if self.dataset_name == "mariadb":
            return [self.config.mariadb_dataset]
        return [self.config.benches_dataset, self.config.mariadb_dataset]


class VolumeDestroySnapshotCommand:
    def __init__(self, config: VolumeConfig, tag: str, dataset_name: str, confirmed: bool) -> None:
        self.config = config
        self.tag = tag
        self.dataset_name = dataset_name
        self.confirmed = confirmed

    def run(self) -> None:
        _require_enabled(self.config)
        if not self.confirmed:
            raise BenchError("Pass --yes to confirm snapshot deletion.")
        from bench_cli.managers.volume_manager import VolumeManager

        manager = VolumeManager(self.config)
        dataset = self._resolve_dataset()
        manager.destroy_snapshot(dataset, self.tag)
        print(f"Snapshot destroyed: {dataset}@{self.tag}")

    def _resolve_dataset(self) -> str:
        if self.dataset_name == "mariadb":
            return self.config.mariadb_dataset
        return self.config.benches_dataset
