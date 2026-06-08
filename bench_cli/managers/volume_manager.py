from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bench_cli.config.volume_config import VolumeConfig
from bench_cli.exceptions import CommandError, VolumeError
from bench_cli.platform import get_package_manager
from bench_cli.utils import run_command


@dataclass
class SnapshotInfo:
    name: str
    dataset: str
    snapshot_tag: str
    created_at: datetime
    used_bytes: int


class VolumeManager:
    def __init__(self, config: VolumeConfig) -> None:
        self.config = config

    def _ensure_zfs(self):
        if shutil.which("zfs"):
            return

        print("ZFS not found installing....")
        pkg_manager = get_package_manager()
        pkg_manager.install("zfsutils-linux")

        if not shutil.which("zfs"):
            raise VolumeError("Something went wrong in installing zfs")
        print("ZFS installed....")

    def pool_exists(self) -> bool:
        try:
            self._run(["sudo", "zpool", "list", "-H", self.config.pool])
            return True
        except VolumeError:
            return False

    def create_pool(self) -> None:
        print(f"Creating pool {self.config.pool}")
        if self.pool_exists():
            print(f"Found existing pool {self.config.pool}")
            return
        self._run(["sudo", "zpool", "create", self.config.pool, self.config.device])
        print(f"Created pool {self.config.pool}")

    def dataset_exists(self, dataset: str) -> bool:
        try:
            self._run(["zfs", "list", "-H", dataset])
            return True
        except VolumeError:
            return False

    def create_dataset(self, dataset: str) -> None:
        if self.dataset_exists(dataset):
            return
        self._run(["sudo", "zfs", "create", dataset])

    def get_used_bytes(self, dataset: str) -> int:
        result = self._run(["sudo", "zfs", "get", "-H", "-p", "-o", "value", "used", dataset])
        return int(result.stdout.decode().strip())

    @staticmethod
    def _parse_size_bytes(size_str: str) -> int:
        s = size_str.strip().upper()
        for suffix, mult in [("P", 1024**5), ("T", 1024**4), ("G", 1024**3), ("M", 1024**2), ("K", 1024)]:
            if s.endswith(suffix):
                return int(float(s[: -len(suffix)]) * mult)
        return int(s)

    def validate_quota(self, dataset: str, quota: str) -> str | None:
        """Return an error string if quota is less than the dataset's current used size, else None."""
        if quota.lower() in ("none", "0"):
            return None
        if not self.dataset_exists(dataset):
            return None
        try:
            used = self.get_used_bytes(dataset)
            new_quota = self._parse_size_bytes(quota)
            if new_quota < used:
                used_g = round(used / 1024**3, 2)
                name = dataset.split("/")[-1]
                return f"Quota {quota} is less than current used size ({used_g}G) for {name} dataset"
        except Exception:
            pass
        return None

    def set_quota(self, dataset: str, quota: str) -> None:
        self._run(["sudo", "zfs", "set", f"quota={quota}", dataset])

    def set_reservation(self, dataset: str, reservation: str) -> None:
        self._run(["sudo", "zfs", "set", f"reservation={reservation}", dataset])

    def set_recordsize(self, dataset: str, recordsize: str) -> None:
        self._run(["sudo", "zfs", "set", f"recordsize={recordsize}", dataset])

    def get_mountpoint(self, dataset: str) -> Path:
        result = self._run(["sudo", "zfs", "get", "-H", "-o", "value", "mountpoint", dataset])
        return Path(result.stdout.decode().strip())

    def set_mountpoint(self, dataset: str, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        self._run(["sudo", "zfs", "set", f"mountpoint={target}", dataset])

    def migrate_data(self, dataset: str, source: Path) -> None:
        print(f"Migrating {source} to ZFS dataset {dataset}...")
        current_mount = self.get_mountpoint(dataset)
        self._run(["sudo", "rsync", "-a", f"{source}/", f"{current_mount}/"])
        print("Data migration complete.")

    def snapshot(self, dataset: str, tag: str) -> None:
        if not self.config.snapshots.enabled:
            raise VolumeError("Snapshots are disabled. Set volume.snapshots.enabled = true in bench.toml to enable.")
        self._run(["sudo", "zfs", "snapshot", f"{dataset}@{tag}"])

    def rollback_snapshot(self, dataset: str, tag: str) -> None:
        if not self._snapshot_exists(f"{dataset}@{tag}"):
            raise VolumeError(f"Snapshot '{dataset}@{tag}' does not exist.")
        self._run(["sudo", "zfs", "rollback", "-r", f"{dataset}@{tag}"])

    def list_snapshots(self, dataset: str) -> list[SnapshotInfo]:
        try:
            result = self._run(["zfs", "list", "-H", "-p", "-t", "snapshot", "-o", "name,creation,used", dataset])
        except VolumeError:
            return []
        output = result.stdout.decode()
        if not output.strip():
            return []
        return [self._parse_snapshot(line) for line in output.splitlines() if line.strip()]

    def destroy_snapshot(self, dataset: str, tag: str) -> None:
        snapshot = f"{dataset}@{tag}"
        if not self._snapshot_exists(snapshot):
            raise VolumeError(f"Snapshot '{snapshot}' does not exist.")
        self._run(["sudo", "zfs", "destroy", snapshot])

    def setup(self) -> None:
        self._ensure_zfs()
        self.create_pool()
        self._setup_dataset(self.config.benches_dataset, self.config.benches.quota, self.config.benches.reservation)
        self._setup_dataset(self.config.mariadb_dataset, self.config.mariadb.quota, self.config.mariadb.reservation)
        # https://www.usenix.org/system/files/login/articles/login_winter16_09_jude.pdf
        # Mariadb default page size 16k zfs defaults to 128k introducing massive io ops therefore force tune it
        self.set_recordsize(self.config.mariadb_dataset, "16K")

    def _setup_dataset(self, dataset: str, quota: str, reservation: str) -> None:
        print(f"Creating dataset {dataset} with quota {quota} and reservation {reservation}")
        self.create_dataset(dataset)
        self.set_quota(dataset, quota)
        self.set_reservation(dataset, reservation)

    def _snapshot_exists(self, snapshot: str) -> bool:
        try:
            self._run(["zfs", "list", "-H", "-t", "snapshot", snapshot])
            return True
        except VolumeError:
            return False

    def _parse_snapshot(self, line: str) -> SnapshotInfo:
        parts = line.split("\t")
        full_name = parts[0]
        dataset, snapshot_tag = full_name.split("@", 1)
        created_at = datetime.fromtimestamp(int(parts[1])) if len(parts) > 1 else datetime.now()
        used_bytes = int(parts[2]) if len(parts) > 2 else 0
        return SnapshotInfo(
            name=full_name,
            dataset=dataset,
            snapshot_tag=snapshot_tag,
            created_at=created_at,
            used_bytes=used_bytes,
        )

    def _run(self, command: str | list[str]):
        argv = command if isinstance(command, list) else shlex.split(command)
        try:
            return run_command(argv)
        except CommandError as e:
            raise VolumeError(f"Command failed: {' '.join(argv)} with: {e!s}")
