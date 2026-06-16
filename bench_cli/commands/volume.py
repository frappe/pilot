from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from bench_cli.config.volume_config import VolumeConfig
from bench_cli.exceptions import BenchError, CommandError
from bench_cli.managers.volume_manager import VolumeManager
from bench_cli.platform import is_linux
from bench_cli.utils import run_command

if TYPE_CHECKING:
    from bench_cli.config.bench_config import BenchConfig
    from bench_cli.core.bench import Bench
    from bench_cli.managers.snapshot_orchestrator import SnapshotOrchestrator


def _ask_dataset() -> str | None:
    print("Which dataset would you like to snapshot?")
    print("  [1] benches")
    print("  [2] mariadb")
    print("  [3] both (default)")
    choice = input("Enter choice [1/2/3]: ").strip()
    if choice == "1":
        return "benches"
    if choice == "2":
        return "mariadb"
    return None


def _resolve_dataset(config: VolumeConfig, dataset_name: str) -> str:
    if dataset_name == "mariadb":
        return config.mariadb_dataset
    return config.benches_dataset


def _target_datasets(config: VolumeConfig, dataset_name: str | None) -> list[str]:
    if dataset_name == "benches":
        return [config.benches_dataset]
    if dataset_name == "mariadb":
        return [config.mariadb_dataset]
    return [config.benches_dataset, config.mariadb_dataset]


def _build_orchestrator(bench: Bench) -> SnapshotOrchestrator:
    from bench_cli.managers.mariadb_manager import MariaDBManager
    from bench_cli.managers.snapshot_orchestrator import SnapshotOrchestrator

    volume = VolumeManager(bench.config.volume)
    mariadb = MariaDBManager(bench.config.mariadb)
    return SnapshotOrchestrator(volume, mariadb, bench)


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
    def __init__(self, config: VolumeConfig, bench_path: Path, bench_config: "BenchConfig | None" = None) -> None:
        self.config = config
        self.bench_path = bench_path
        self.bench_config = bench_config

    def setup_mariadb(self, manager: VolumeManager):
        data_dir = Path(self.config.mariadb.data_dir)
        has_data = data_dir.exists() and any(data_dir.iterdir())

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

    def _is_pool_in_use(self) -> None:
        if self.bench_config is None:
            return

        from bench_cli.config.bench_config import BenchConfig

        me = self.bench_path.resolve()
        parent_dir = self.bench_path.parent.resolve()

        for sibling in parent_dir.iterdir():
            if not sibling.is_dir() or sibling.resolve() == me:
                continue

            sibling_bench_config_path = sibling / "bench.toml"
            if not sibling_bench_config_path.exists():
                continue

            sibling_bench_config = BenchConfig.from_file(sibling_bench_config_path)
            if sibling_bench_config.volume.pool == self.bench_config.volume.pool:
                raise BenchError(f"Pool {self.bench_config.volume.pool} is already in use by {sibling_bench_config.name}")

    def run(self) -> None:
        if not is_linux():
            raise BenchError("Volume management requires Linux (ZFS is not supported on macOS).")

        # Throw an error in case this pool is already in use by some other bench
        self._is_pool_in_use()
        self._resolve_backing()

        manager = VolumeManager(self.config)
        manager.setup()
        self.setup_mariadb(manager)
        self.setup_bench(manager)

        print("Volume setup complete.")

    def _resolve_backing(self) -> None:
        from bench_cli.managers.volume_manager import resolve_auto_backing

        choice = resolve_auto_backing(self.config)
        if not choice:
            return
        print(f"  {choice}")
        if self.bench_config is not None:
            from bench_cli.config.toml_writer import bench_config_to_toml

            (self.bench_path / "bench.toml").write_text(bench_config_to_toml(self.bench_config))
            print("  Saved resolved volume settings to bench.toml")


class VolumeStatusCommand:
    def __init__(self, config: VolumeConfig) -> None:
        self.config = config

    def run(self) -> None:
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
    def __init__(self, bench: Bench, dataset_name: str | None) -> None:
        self.bench = bench
        self.config = bench.config.volume
        self.dataset_name = dataset_name

    def run(self) -> None:
        dataset_name = self.dataset_name if self.dataset_name is not None else _ask_dataset()
        orchestrator = _build_orchestrator(self.bench)
        tag = datetime.now().strftime("%Y%m%d-%H%M%S")
        for dataset in _target_datasets(self.config, dataset_name):
            orchestrator.create_snapshot(dataset, tag)
            print(f"Snapshot created: {dataset}@{tag}")


class VolumeListSnapshotsCommand:
    def __init__(self, config: VolumeConfig, dataset_name: str | None) -> None:
        self.config = config
        self.dataset_name = dataset_name

    def run(self) -> None:
        manager = VolumeManager(self.config)
        for dataset in _target_datasets(self.config, self.dataset_name):
            snapshots = manager.list_snapshots(dataset)
            print(f"Dataset: {dataset}")
            if not snapshots:
                print("  (no snapshots)")
                continue
            for snap in snapshots:
                used_mb = snap.used_bytes // (1024 * 1024)
                ts = snap.created_at.strftime("%Y-%m-%d %H:%M:%S")
                print(f"  {snap.snapshot_tag:<30} created: {ts}  used: {used_mb}M")


class VolumeDestroySnapshotCommand:
    def __init__(self, config: VolumeConfig, tag: str, dataset_name: str) -> None:
        self.config = config
        self.tag = tag
        self.dataset_name = dataset_name

    def run(self) -> None:
        dataset = _resolve_dataset(self.config, self.dataset_name)
        VolumeManager(self.config).destroy_snapshot(dataset, self.tag)
        print(f"Snapshot destroyed: {dataset}@{self.tag}")


class VolumeRestoreSnapshotCommand:
    def __init__(self, bench: Bench, tag: str, dataset_name: str) -> None:
        self.bench = bench
        self.config = bench.config.volume
        self.tag = tag
        self.dataset_name = dataset_name

    def run(self) -> None:
        dataset = _resolve_dataset(self.config, self.dataset_name)
        print(f"Restoring {dataset} to snapshot {self.tag}...")
        self._warn(dataset)
        _build_orchestrator(self.bench).rollback_snapshot(dataset, self.tag)
        print(f"Restored {dataset}@{self.tag}.")

    def _warn(self, dataset: str) -> None:
        print("Sites will be put into maintenance mode during restore.")
        if dataset == self.config.mariadb_dataset:
            print("MariaDB will be stopped and restarted during restore.")
