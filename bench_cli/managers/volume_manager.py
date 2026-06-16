from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
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


@dataclass
class DiskInfo:
    path: str
    size_bytes: int
    has_signature: bool = False  # leftover partitions/filesystem labels — wiped on pool creation


@dataclass
class DatasetInfo:
    name: str
    mountpoint: str


@dataclass
class PoolInfo:
    name: str
    size_bytes: int
    device: str
    datasets: list[DatasetInfo] = field(default_factory=list)


# Smart sizing policy: strict 60/40 quota split between benches and mariadb,
# 10/5 reservations, image sized at 75% of the root filesystem's free space.
_MIN_USABLE_DISK_BYTES = 10 * 1024**3
_BENCHES_QUOTA_FRACTION = 0.60
_MARIADB_QUOTA_FRACTION = 0.40
_BENCHES_RESERVATION_FRACTION = 0.10
_MARIADB_RESERVATION_FRACTION = 0.05
_IMAGE_FREE_SPACE_FRACTION = 0.75


def discover_unused_disks() -> list[DiskInfo]:
    """Block devices safe to hand to ZFS: whole disks where nothing is mounted
    anywhere on the disk, no active storage stack (LVM/RAID/dm-crypt) sits on
    top of it, and it is not a member of an imported ZFS pool.

    Leftover partitions or filesystem signatures — e.g. from a destroyed pool
    or an old install — do NOT disqualify a disk: ``zpool create -f`` wipes
    them. Such disks are flagged via ``has_signature`` so the UI can warn.
    The root disk is excluded because its partitions are mounted. Runs without
    sudo and is best-effort: returns [] on any failure. Largest first.
    """
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-b", "-o", "NAME,TYPE,SIZE,RO,MOUNTPOINTS,FSTYPE"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []
        devices = json.loads(result.stdout).get("blockdevices", [])
    except (OSError, ValueError):
        return []
    active_pool_disks = {pool.device for pool in existing_pools()}
    disks = [
        DiskInfo(
            path=f"/dev/{device['name']}",
            size_bytes=int(device["size"]),
            has_signature=bool(device.get("fstype") or device.get("children")),
        )
        for device in devices
        if device.get("type") == "disk"
        and not device.get("ro")
        and int(device.get("size") or 0) >= _MIN_USABLE_DISK_BYTES
        and f"/dev/{device['name']}" not in active_pool_disks
        and not _anything_mounted(device)
        and not _has_storage_stack(device)
    ]
    return sorted(disks, key=lambda disk: disk.size_bytes, reverse=True)


def _anything_mounted(device: dict) -> bool:
    if any(device.get("mountpoints") or []):
        return True
    return any(_anything_mounted(child) for child in device.get("children") or [])


def _has_storage_stack(device: dict) -> bool:
    """True when something deeper than plain partitions sits on the disk
    (LVM volumes, RAID members, dm-crypt) — those may be active without a
    visible mountpoint, so the disk is not safe to wipe."""
    return any(child.get("type") != "part" or _has_storage_stack(child) for child in device.get("children") or [])


def existing_pools() -> list[PoolInfo]:
    """ZFS pools already imported on this machine, with their backing disk.

    A disk hosting a live pool is excluded from :func:`discover_unused_disks`
    (it is busy), but for setup it is the best suggestion of all — re-running
    the wizard or init on a machine that already has a bench pool should reuse
    it rather than fall back to an image file. Unprivileged and best-effort.
    """
    try:
        result = subprocess.run(
            ["zpool", "list", "-H", "-p", "-o", "name,size"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []
    except OSError:
        return []
    pools = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            pools.append(
                PoolInfo(
                    name=parts[0],
                    size_bytes=int(parts[1]),
                    device=_pool_backing_device(parts[0]),
                    datasets=_pool_datasets(parts[0]),
                )
            )
        except ValueError:
            continue
    return pools


def _pool_datasets(pool: str) -> list[DatasetInfo]:
    """Datasets in the pool with their current mountpoints.

    Lets setup spot a dataset already mounted where it wants to put one — most
    importantly ``/var/lib/mysql``, where re-running on a machine that already
    has a bench pool would otherwise collide on ``zfs set mountpoint``.
    Unprivileged and best-effort: returns [] on any failure.
    """
    try:
        result = subprocess.run(
            ["zfs", "list", "-H", "-r", "-o", "name,mountpoint", pool],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []
    except OSError:
        return []
    datasets = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        datasets.append(DatasetInfo(name=parts[0], mountpoint=parts[1]))
    return datasets


def _pool_backing_device(pool: str) -> str:
    """The pool's first vdev, mapped to its parent disk when it's a partition."""
    try:
        result = subprocess.run(["zpool", "list", "-v", "-H", "-P", pool], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return ""
        vdev = next(
            (line.strip().split("\t")[0] for line in result.stdout.splitlines()[1:] if line.strip().startswith("/")),
            "",
        )
        if not vdev:
            return ""
        parent = subprocess.run(["lsblk", "-no", "PKNAME", vdev], capture_output=True, text=True, check=False)
        name = parent.stdout.strip().splitlines()[0].strip() if parent.returncode == 0 and parent.stdout.strip() else ""
        return f"/dev/{name}" if name else vdev
    except (OSError, IndexError):
        return ""


def list_device_choices() -> list[dict]:
    """Devices the wizard can offer: disks hosting existing pools first, then unused disks."""
    choices = [{"path": pool.device, "size_bytes": pool.size_bytes, "pool": pool.name} for pool in existing_pools() if pool.device]
    choices += [{"path": disk.path, "size_bytes": disk.size_bytes, "has_signature": disk.has_signature} for disk in discover_unused_disks()]
    return choices


def rootfs_free_bytes() -> int:
    return shutil.disk_usage("/").free


def default_image_size_bytes() -> int:
    return max(int(rootfs_free_bytes() * 0.75), 10 * 1024**3)


def smart_dataset_sizes(backing_bytes: int) -> dict:
    """Quota/reservation defaults derived from the backing size (flat wizard keys)."""
    return {
        "volume_benches_quota": _whole_gigabytes(backing_bytes * _BENCHES_QUOTA_FRACTION),
        "volume_mariadb_quota": _whole_gigabytes(backing_bytes * _MARIADB_QUOTA_FRACTION),
        "volume_benches_reservation": _whole_gigabytes(backing_bytes * _BENCHES_RESERVATION_FRACTION),
        "volume_mariadb_reservation": _whole_gigabytes(backing_bytes * _MARIADB_RESERVATION_FRACTION),
    }


def compute_smart_defaults() -> dict:
    """Wizard defaults, in order of preference: reuse a disk that already
    hosts a ZFS pool, else device backing on the largest unused disk, else
    image backing at 75% of rootfs free space. Includes the device choices so
    the UI can offer a dropdown."""
    pools = existing_pools()
    disks = discover_unused_disks()
    if pools:
        backing_bytes = pools[0].size_bytes
        defaults = {"volume_backing": "device", "volume_device": pools[0].device, "volume_pool": pools[0].name}
    elif disks:
        backing_bytes = disks[0].size_bytes
        defaults = {"volume_backing": "device", "volume_device": disks[0].path}
    else:
        backing_bytes = default_image_size_bytes()
        defaults = {"volume_backing": "image", "volume_image_size": _whole_gigabytes(backing_bytes)}
    defaults.update(smart_dataset_sizes(backing_bytes))
    defaults["available_devices"] = [{"path": pool.device, "size_bytes": pool.size_bytes, "pool": pool.name} for pool in pools if pool.device] + [
        {"path": disk.path, "size_bytes": disk.size_bytes, "has_signature": disk.has_signature} for disk in disks
    ]
    return defaults


def resolve_auto_backing(config: VolumeConfig) -> str:
    """Resolve backing = "auto" in place; return a description of the choice.

    Auto backing implies auto sizing: quotas and reservations are always
    recomputed from the resolved backing size. Set backing explicitly to
    control sizes manually.
    """
    if config.backing != "auto":
        return ""
    pool_match = next((p for p in existing_pools() if p.name == config.pool and p.device), None)
    disks = discover_unused_disks()
    if pool_match:
        config.backing = "device"
        config.device = pool_match.device
        backing_bytes = pool_match.size_bytes
        choice = f"Found existing pool {pool_match.name} on {pool_match.device} — reusing it"
    elif disks:
        config.backing = "device"
        config.device = disks[0].path
        backing_bytes = disks[0].size_bytes
        choice = f"Found unused disk {config.device} ({_whole_gigabytes(backing_bytes)}) — using device backing"
    else:
        backing_bytes = default_image_size_bytes()
        config.backing = "image"
        config.image.size = _whole_gigabytes(backing_bytes)
        choice = f"No unused disk found — using a {config.image.size} image file at {config.image_path}"
    config.benches.quota = _whole_gigabytes(backing_bytes * _BENCHES_QUOTA_FRACTION)
    config.mariadb.quota = _whole_gigabytes(backing_bytes * _MARIADB_QUOTA_FRACTION)
    config.benches.reservation = _whole_gigabytes(backing_bytes * _BENCHES_RESERVATION_FRACTION)
    config.mariadb.reservation = _whole_gigabytes(backing_bytes * _MARIADB_RESERVATION_FRACTION)
    return choice


def _whole_gigabytes(num_bytes: float) -> str:
    return f"{max(1, int(num_bytes // 1024**3))}G"


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
            self._run(["zpool", "list", "-H", self.config.pool])
            return True
        except VolumeError:
            return False

    def create_pool(self) -> None:
        print(f"Creating pool {self.config.pool}")
        if self.pool_exists():
            print(f"Found existing pool {self.config.pool}")
            return
        vdev = self.config.device if self.config.backing == "device" else self._ensure_image_file()
        # -f: the device may carry leftover partitions or labels (e.g. a destroyed
        # pool). Discovery only offers disks with nothing mounted and no active
        # storage stack, and an explicitly configured device is the user's call.
        self._run(["sudo", "zpool", "create", "-f", self.config.pool, vdev])
        print(f"Created pool {self.config.pool}")

    def _ensure_image_file(self) -> str:
        """Create the preallocated backing image file if missing; return its path.

        Preallocated (fallocate, never sparse) so the pool cannot be corrupted
        later by the root filesystem filling up — setup fails fast instead.
        """
        path = self.config.image_path
        if Path(path).exists():
            print(f"Found existing image file {path}")
            return path
        print(f"Creating {self.config.image.size} image file at {path}")
        self._run(["sudo", "mkdir", "-p", str(Path(path).parent)])
        self._run(["sudo", "fallocate", "-l", self.config.image.size, path])
        return path

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
        result = self._run(["zfs", "get", "-H", "-p", "-o", "value", "used", dataset])
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

    @classmethod
    def validate_reservation_within_quota(cls, reservation: str, quota: str, dataset_name: str = "") -> str | None:
        """Return an error if the reservation exceeds the quota, else None.

        Pure size arithmetic — no ZFS calls — so it is safe to run at config
        validation time, in the setup wizard, and before the pool exists. ZFS
        itself rejects a reservation larger than the quota.
        """
        if quota.lower() in ("none", "0") or reservation.lower() in ("none", "0"):
            return None
        try:
            res_bytes = cls._parse_size_bytes(reservation)
            quota_bytes = cls._parse_size_bytes(quota)
        except Exception:
            return None
        if res_bytes > quota_bytes:
            label = f" for {dataset_name} dataset" if dataset_name else ""
            return f"Reservation {reservation} cannot exceed quota {quota}{label}"
        return None

    def backing_size_bytes(self) -> int | None:
        """Size of the pool's backing storage in bytes.

        Device backing: the block device size via ``lsblk``. Image backing: the
        image file's size if it exists, else the configured ``image.size``.
        Read-only and unprivileged — no sudo — so it is safe to call from the
        admin web process. Returns ``None`` if it cannot be determined, so
        callers skip the check rather than raise a false positive.
        """
        if self.config.backing == "image":
            return self._image_size_bytes()
        return self._device_size_bytes()

    def _device_size_bytes(self) -> int | None:
        if not self.config.device:
            return None
        try:
            result = subprocess.run(
                ["lsblk", "-bdno", "SIZE", self.config.device],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return None
            return int(result.stdout.strip().splitlines()[0])
        except (OSError, ValueError, IndexError):
            return None

    def _image_size_bytes(self) -> int | None:
        try:
            return Path(self.config.image_path).stat().st_size
        except OSError:
            pass
        try:
            return self._parse_size_bytes(self.config.image.size)
        except Exception:
            return None

    def validate_sizes_fit_backing(self) -> str | None:
        """Return an error if any quota/reservation exceeds the backing size, else None.

        For image backing also pre-flights that the root filesystem has enough
        free space to preallocate the image file (when it doesn't exist yet).
        """
        if error := self._validate_image_fits_filesystem():
            return error
        backing_bytes = self.backing_size_bytes()
        if backing_bytes is None:
            return None
        backing_label = "image size" if self.config.backing == "image" else "device size"
        backing_g = round(backing_bytes / 1024**3, 2)
        for label, dataset in (("benches", self.config.benches), ("mariadb", self.config.mariadb)):
            for kind, value in (("reservation", dataset.reservation), ("quota", dataset.quota)):
                if value.lower() in ("none", "0"):
                    continue
                try:
                    size = self._parse_size_bytes(value)
                except Exception:
                    continue
                if size > backing_bytes:
                    return f"{label} {kind} {value} exceeds {backing_label} ({backing_g}G)"
        return None

    def _validate_image_fits_filesystem(self) -> str | None:
        if self.config.backing != "image" or not self.config.image.size:
            return None
        image = Path(self.config.image_path)
        if image.exists():
            return None
        try:
            size = self._parse_size_bytes(self.config.image.size)
        except Exception:
            return None
        ancestor = image.parent
        while not ancestor.exists():
            ancestor = ancestor.parent
        free = shutil.disk_usage(ancestor).free
        if size > free:
            free_g = round(free / 1024**3, 2)
            return f"Image size {self.config.image.size} exceeds free space on the root filesystem ({free_g}G available)"
        return None

    # ── settings-modal helpers ──────────────────────────────────────────────

    def _dataset_configs(self) -> list[tuple[str, object]]:
        return [(self.config.benches_dataset, self.config.benches), (self.config.mariadb_dataset, self.config.mariadb)]

    def validate_quotas_above_usage(self) -> str | None:
        """Ensure no configured quota is below its dataset's current used size."""
        for dataset, cfg in self._dataset_configs():
            if error := self.validate_quota(dataset, cfg.quota):
                return error
        return None

    def apply_sizes(self) -> str | None:
        """Apply the configured quota/reservation to existing datasets (idempotent)."""
        for dataset, cfg in self._dataset_configs():
            if not self.dataset_exists(dataset):
                continue
            try:
                self.set_quota(dataset, cfg.quota)
                self.set_reservation(dataset, cfg.reservation)
            except VolumeError as error:
                return str(error)
        return None

    def set_quota(self, dataset: str, quota: str) -> None:
        self._run(["sudo", "zfs", "set", f"quota={quota}", dataset])

    def set_reservation(self, dataset: str, reservation: str) -> None:
        self._run(["sudo", "zfs", "set", f"reservation={reservation}", dataset])

    def set_recordsize(self, dataset: str, recordsize: str) -> None:
        self._run(["sudo", "zfs", "set", f"recordsize={recordsize}", dataset])

    def get_mountpoint(self, dataset: str) -> Path:
        result = self._run(["zfs", "get", "-H", "-o", "value", "mountpoint", dataset])
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

    def _is_pool_already_configured(self) -> bool:
        pools = existing_pools()

        for pool in pools:
            for dataset_info in pool.datasets:
                # Two active datasets of the same name are not allowed
                if dataset_info.name == self.config.mariadb_dataset or dataset_info.name == self.config.benches_dataset:
                    return True

        return False

    def setup(self) -> None:
        self._ensure_zfs()
        if self._is_pool_already_configured():
            print("Pool is already configured skipping")
            return

        print(f"Creating ZFS pool '{self.config.pool}' and datasets...")

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
