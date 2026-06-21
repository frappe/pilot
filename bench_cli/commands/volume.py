from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from bench_cli.commands.base import Command
from bench_cli.exceptions import BenchError, CommandError

from bench_cli.utils import run_command

if TYPE_CHECKING:
    from bench_cli.config.bench_config import BenchConfig
    from bench_cli.config.volume_config import VolumeConfig
    from bench_cli.core.bench import Bench
    from bench_cli.managers.snapshot_orchestrator import SnapshotOrchestrator
    from bench_cli.managers.volume_manager import VolumeManager


def _build_orchestrator(bench: Bench) -> SnapshotOrchestrator:
    from bench_cli.managers.mariadb_manager import MariaDBManager
    from bench_cli.managers.snapshot_orchestrator import SnapshotOrchestrator
    from bench_cli.managers.volume_manager import VolumeManager

    volume = VolumeManager(bench.config.volume)
    mariadb = MariaDBManager(bench.config.mariadb)
    return SnapshotOrchestrator(volume, mariadb, bench)


def _stop_mariadb(manager=None) -> None:
    from bench_cli.platform import service_command

    try:
        if manager is not None:
            manager.stop()
        else:
            run_command(service_command("stop", "mariadb"))
    except CommandError:
        pass


def _start_mariadb(manager=None) -> None:
    from bench_cli.platform import service_command

    try:
        if manager is not None:
            manager.start()
        else:
            run_command(service_command("start", "mariadb"))
    except CommandError as e:
        print(f"Warning: failed to restart MariaDB service: {e}")


class VolumeSetupCommand:
    def __init__(self, config: VolumeConfig, bench_path: Path, bench_config: "BenchConfig | None" = None) -> None:
        self.config = config
        self.bench_path = bench_path
        self.bench_config = bench_config

    def run(self) -> None:
        from bench_cli.managers.volume_manager import VolumeManager
        from bench_cli.platform import is_linux

        if not is_linux():
            raise BenchError("Volume management requires Linux (ZFS is not supported on macOS).")

        self._resolve_backing()

        manager = VolumeManager(self.config)
        manager.setup()
        self._setup_bind_mounts(manager)

        print("Volume setup complete.")

    def _setup_bind_mounts(self, manager: "VolumeManager") -> None:
        """Expose the single dataset's `benches`/`mariadb` subdirs at their
        conventional paths via bind mounts, so both live on one dataset and a
        snapshot/rollback is atomic across the bench files and the database."""
        mount = manager.get_mountpoint(self.config.dataset_path)
        self._bind_bench(manager, mount)
        self._bind_mariadb(manager, mount)

    def _bind_bench(self, manager: "VolumeManager", mount: Path) -> None:
        sub = mount / "benches"
        run_command(["sudo", "mkdir", "-p", str(sub)])
        run_command(["sudo", "chown", "--reference", str(self.bench_path), str(sub)])
        # Migrate the bench's current files into the dataset before the bind
        # mount shadows the on-disk directory. Runs before "Create bench
        # directory structure" in init, so there's little to copy on a fresh bench.
        run_command(["sudo", "rsync", "-a", f"{self.bench_path}/", f"{sub}/"])
        manager.bind_mount(sub, self.bench_path)
        manager.persist_bind_mount(sub, self.bench_path)

    def _bind_mariadb(self, manager: "VolumeManager", mount: Path) -> None:
        sub = mount / "mariadb"
        db_manager = self._mariadb_manager()
        datadir = Path(db_manager.data_dir() if db_manager else "/var/lib/mysql")
        run_command(["sudo", "install", "-d", "-m", "750", "-o", "mysql", "-g", "mysql", str(sub)])

        has_data = datadir.exists() and any(datadir.iterdir())
        if has_data:
            print(f"Existing data found at {datadir}, stopping MariaDB for migration...")
            _stop_mariadb(db_manager)
            run_command(["sudo", "rsync", "-a", f"{datadir}/", f"{sub}/"])

        manager.bind_mount(sub, datadir)
        manager.persist_bind_mount(sub, datadir)

        if has_data:
            _start_mariadb(db_manager)

    def _mariadb_manager(self):
        if self.bench_config is None or not self.bench_config.mariadb.instance:
            return None
        from bench_cli.managers.mariadb_manager import MariaDBManager

        return MariaDBManager(self.bench_config.mariadb)

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


class VolumeStatusCommand(Command):
    name = "status"
    help = "Show pool and dataset status."
    group = "volume"

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench.config.volume)

    def __init__(self, config: VolumeConfig) -> None:
        self.config = config

    def run(self) -> None:
        self._print_pool()
        self._print_dataset(self.config.dataset_path)

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


class VolumeSnapshotCommand(Command):
    name = "snapshot"
    help = "Create a snapshot of the bench (files + database)."
    group = "volume"

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench)

    def __init__(self, bench: Bench) -> None:
        self.bench = bench
        self.config = bench.config.volume

    def run(self) -> None:
        orchestrator = _build_orchestrator(self.bench)
        tag = datetime.now().strftime("%Y%m%d-%H%M%S")
        orchestrator.create_snapshot(tag)
        print(f"Snapshot created: {self.config.dataset_path}@{tag}")


class VolumeListSnapshotsCommand(Command):
    name = "list-snapshots"
    help = "List snapshots."
    group = "volume"

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench.config.volume)

    def __init__(self, config: VolumeConfig) -> None:
        self.config = config

    def run(self) -> None:
        from bench_cli.managers.volume_manager import VolumeManager

        manager = VolumeManager(self.config)
        snapshots = manager.list_snapshots(self.config.dataset_path)
        print(f"Dataset: {self.config.dataset_path}")
        if not snapshots:
            print("  (no snapshots)")
            return
        for snap in snapshots:
            used_mb = snap.used_bytes // (1024 * 1024)
            ts = snap.created_at.strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {snap.snapshot_tag:<30} created: {ts}  used: {used_mb}M")


class VolumeDestroySnapshotCommand(Command):
    name = "destroy-snapshot"
    help = "Destroy a snapshot."
    group = "volume"

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("tag", help="Snapshot tag to destroy (e.g. 20250528-140000).")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench.config.volume, args.tag)

    def __init__(self, config: VolumeConfig, tag: str) -> None:
        self.config = config
        self.tag = tag

    def run(self) -> None:
        from bench_cli.managers.volume_manager import VolumeManager

        VolumeManager(self.config).destroy_snapshot(self.config.dataset_path, self.tag)
        print(f"Snapshot destroyed: {self.config.dataset_path}@{self.tag}")


class VolumeRestoreSnapshotCommand(Command):
    name = "restore-snapshot"
    help = "Restore the bench to a snapshot."
    group = "volume"

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("tag", help="Snapshot tag to restore to (e.g. 20250528-140000).")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, args.tag)

    def __init__(self, bench: Bench, tag: str) -> None:
        self.bench = bench
        self.config = bench.config.volume
        self.tag = tag

    def run(self) -> None:
        print(f"Restoring {self.config.dataset_path} to snapshot {self.tag}...")
        print("Sites will be put into maintenance mode and MariaDB stopped during restore.")
        _build_orchestrator(self.bench).rollback_snapshot(self.tag)
        print(f"Restored {self.config.dataset_path}@{self.tag}.")
