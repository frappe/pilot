import json
import os
from types import SimpleNamespace

import pytest

import pilot.managers.volume_manager as volume_manager
from pilot.config.volume_config import VolumeConfig
from pilot.exceptions import VolumeError
from pilot.managers.volume_manager import (
    DatasetInfo,
    DiskInfo,
    PoolInfo,
    VolumeManager,
    compute_smart_defaults,
    discover_unused_disks,
    resolve_auto_backing,
    smart_dataset_sizes,
)

G = 1024**3


def _disk(name: str, size: int, **overrides) -> dict:
    entry = {"name": name, "type": "disk", "size": size, "ro": False, "mountpoints": [None], "fstype": None}
    entry.update(overrides)
    return entry


def _patch_lsblk(monkeypatch, devices: list[dict], returncode: int = 0) -> None:
    def fake_run(argv, **kwargs):
        return SimpleNamespace(returncode=returncode, stdout=json.dumps({"blockdevices": devices}))

    monkeypatch.setattr(volume_manager.subprocess, "run", fake_run)


def test_discover_includes_clean_disk(monkeypatch) -> None:
    _patch_lsblk(monkeypatch, [_disk("nvme1n1", 100 * G)])
    assert discover_unused_disks() == [DiskInfo(path="/dev/nvme1n1", size_bytes=100 * G)]


def test_discover_sorts_largest_first(monkeypatch) -> None:
    _patch_lsblk(monkeypatch, [_disk("sdb", 50 * G), _disk("sdc", 200 * G)])
    assert [d.path for d in discover_unused_disks()] == ["/dev/sdc", "/dev/sdb"]


def test_discover_excludes_root_disk_with_mounted_partition(monkeypatch) -> None:
    root = _disk("nvme0n1", 20 * G, children=[{"name": "nvme0n1p1", "type": "part", "mountpoints": ["/"], "fstype": "ext4"}])
    _patch_lsblk(monkeypatch, [root])
    assert discover_unused_disks() == []


def test_discover_includes_disk_with_stale_signature(monkeypatch) -> None:
    # A destroyed pool leaves zfs_member labels/partitions behind — still usable, but flagged.
    stale = _disk("nvme1n1", 50 * G, children=[{"name": "nvme1n1p1", "type": "part", "mountpoints": [None], "fstype": "zfs_member"}])
    _patch_lsblk(monkeypatch, [stale])
    monkeypatch.setattr(volume_manager, "existing_pools", lambda: [])
    assert discover_unused_disks() == [DiskInfo(path="/dev/nvme1n1", size_bytes=50 * G, has_signature=True)]


def test_discover_excludes_active_pool_member(monkeypatch) -> None:
    # An imported pool's disk shows no mountpoints in lsblk — only zpool knows it's busy.
    member = _disk("nvme1n1", 50 * G, children=[{"name": "nvme1n1p1", "type": "part", "mountpoints": [None], "fstype": "zfs_member"}])
    _patch_lsblk(monkeypatch, [member])
    monkeypatch.setattr(volume_manager, "existing_pools", lambda: [PoolInfo("bench-pool", 50 * G, "/dev/nvme1n1")])
    assert discover_unused_disks() == []


def test_discover_excludes_active_storage_stack(monkeypatch) -> None:
    # LVM/RAID can be active without mountpoints — anything deeper than plain partitions is off-limits.
    lvm = _disk(
        "sdb",
        100 * G,
        children=[{"name": "sdb1", "type": "part", "mountpoints": [None], "fstype": "LVM2_member", "children": [{"name": "vg-lv", "type": "lvm", "mountpoints": [None]}]}],
    )
    _patch_lsblk(monkeypatch, [lvm])
    monkeypatch.setattr(volume_manager, "existing_pools", lambda: [])
    assert discover_unused_disks() == []


def test_discover_excludes_mounted_disk(monkeypatch) -> None:
    _patch_lsblk(monkeypatch, [_disk("sdb", 100 * G, mountpoints=["/mnt/data"])])
    assert discover_unused_disks() == []


def test_discover_excludes_non_disks_and_readonly(monkeypatch) -> None:
    loop = _disk("loop0", 50 * G, type="loop")
    readonly = _disk("sdb", 50 * G, ro=True)
    _patch_lsblk(monkeypatch, [loop, readonly])
    assert discover_unused_disks() == []


def test_discover_excludes_tiny_disks(monkeypatch) -> None:
    _patch_lsblk(monkeypatch, [_disk("sdb", 5 * G)])
    assert discover_unused_disks() == []


def test_discover_returns_empty_on_lsblk_failure(monkeypatch) -> None:
    _patch_lsblk(monkeypatch, [], returncode=1)
    assert discover_unused_disks() == []


def test_discover_returns_empty_when_lsblk_missing(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        raise FileNotFoundError("lsblk")

    monkeypatch.setattr(volume_manager.subprocess, "run", fake_run)
    assert discover_unused_disks() == []


# ── smart sizing ──────────────────────────────────────────────────────────────


def test_smart_dataset_sizes_single_dataset() -> None:
    sizes = smart_dataset_sizes(100 * G)
    assert sizes == {
        "volume_quota": "100G",  # quota = whole backing (100%)
        "volume_reservation": "15G",  # 15% guaranteed reservation
    }


def test_smart_dataset_sizes_floor_one_gigabyte() -> None:
    sizes = smart_dataset_sizes(5 * G)
    assert sizes["volume_reservation"] == "1G"  # 15% of 5G = 0.75G -> floored to min 1G


def test_compute_smart_defaults_prefers_device(monkeypatch) -> None:
    monkeypatch.setattr(volume_manager, "existing_pools", lambda: [])
    monkeypatch.setattr(volume_manager, "discover_unused_disks", lambda: [DiskInfo("/dev/sdb", 100 * G)])
    defaults = compute_smart_defaults()
    assert defaults["volume_backing"] == "device"
    assert defaults["volume_device"] == "/dev/sdb"
    assert defaults["volume_quota"] == "100G"
    assert defaults["available_devices"] == [{"path": "/dev/sdb", "size_bytes": 100 * G, "has_signature": False}]


def test_compute_smart_defaults_falls_back_to_image(monkeypatch) -> None:
    monkeypatch.setattr(volume_manager, "existing_pools", lambda: [])
    monkeypatch.setattr(volume_manager, "discover_unused_disks", lambda: [])
    monkeypatch.setattr(volume_manager, "default_image_size_bytes", lambda: 40 * G)
    defaults = compute_smart_defaults()
    assert defaults["volume_backing"] == "image"
    assert defaults["volume_image_size"] == "40G"
    assert defaults["volume_quota"] == "40G"
    assert defaults["volume_reservation"] == "6G"
    assert defaults["available_devices"] == []


def test_default_image_size_is_75_percent_of_free(monkeypatch) -> None:
    monkeypatch.setattr(volume_manager.shutil, "disk_usage", lambda _: SimpleNamespace(free=100 * G))
    assert volume_manager.default_image_size_bytes() == 75 * G


def test_default_image_size_floors_at_10g(monkeypatch) -> None:
    monkeypatch.setattr(volume_manager.shutil, "disk_usage", lambda _: SimpleNamespace(free=8 * G))
    assert volume_manager.default_image_size_bytes() == 10 * G


# ── auto backing resolution ───────────────────────────────────────────────────


def test_resolve_auto_picks_largest_disk(monkeypatch) -> None:
    monkeypatch.setattr(volume_manager, "existing_pools", lambda: [])
    monkeypatch.setattr(volume_manager, "discover_unused_disks", lambda: [DiskInfo("/dev/sdc", 200 * G), DiskInfo("/dev/sdb", 50 * G)])
    config = VolumeConfig(pool="bench-pool", backing="auto")
    choice = resolve_auto_backing(config)
    assert "/dev/sdc" in choice
    assert config.backing == "device"
    assert config.device == "/dev/sdc"
    assert config.dataset.quota == "200G"
    assert config.dataset.reservation == "30G"


def test_resolve_auto_falls_back_to_image(monkeypatch) -> None:
    monkeypatch.setattr(volume_manager, "existing_pools", lambda: [])
    monkeypatch.setattr(volume_manager, "discover_unused_disks", lambda: [])
    monkeypatch.setattr(volume_manager, "default_image_size_bytes", lambda: 40 * G)
    config = VolumeConfig(pool="bench-pool", backing="auto")
    choice = resolve_auto_backing(config)
    assert "image" in choice
    assert config.backing == "image"
    assert config.image.size == "40G"
    assert config.dataset.quota == "40G"
    assert config.dataset.reservation == "6G"


def test_resolve_auto_noop_for_explicit_backing() -> None:
    config = VolumeConfig(pool="bench-pool", backing="device", device="/dev/sdb")
    assert resolve_auto_backing(config) == ""
    assert config.device == "/dev/sdb"
    assert config.dataset.quota == "50G"  # untouched defaults


# ── existing pool reuse ───────────────────────────────────────────────────────


def test_compute_smart_defaults_prefers_existing_pool(monkeypatch) -> None:
    monkeypatch.setattr(volume_manager, "existing_pools", lambda: [PoolInfo("bench-pool", 50 * G, "/dev/nvme1n1")])
    monkeypatch.setattr(volume_manager, "discover_unused_disks", lambda: [DiskInfo("/dev/sdb", 100 * G)])
    defaults = compute_smart_defaults()
    assert defaults["volume_backing"] == "device"
    assert defaults["volume_device"] == "/dev/nvme1n1"
    assert defaults["volume_pool"] == "bench-pool"
    assert defaults["volume_quota"] == "50G"  # the pool size, not the unused disk
    assert defaults["available_devices"] == [
        {"path": "/dev/nvme1n1", "size_bytes": 50 * G, "pool": "bench-pool"},
        {"path": "/dev/sdb", "size_bytes": 100 * G, "has_signature": False},
    ]


def test_resolve_auto_reuses_matching_pool(monkeypatch) -> None:
    monkeypatch.setattr(volume_manager, "existing_pools", lambda: [PoolInfo("bench-pool", 50 * G, "/dev/nvme1n1")])
    monkeypatch.setattr(volume_manager, "discover_unused_disks", lambda: [DiskInfo("/dev/sdb", 100 * G)])
    config = VolumeConfig(pool="bench-pool", backing="auto")
    choice = resolve_auto_backing(config)
    assert "reusing" in choice
    assert config.backing == "device"
    assert config.device == "/dev/nvme1n1"
    assert config.dataset.quota == "50G"


def test_resolve_auto_ignores_pool_with_other_name(monkeypatch) -> None:
    monkeypatch.setattr(volume_manager, "existing_pools", lambda: [PoolInfo("other-pool", 50 * G, "/dev/nvme1n1")])
    monkeypatch.setattr(volume_manager, "discover_unused_disks", lambda: [DiskInfo("/dev/sdb", 100 * G)])
    config = VolumeConfig(pool="bench-pool", backing="auto")
    resolve_auto_backing(config)
    assert config.device == "/dev/sdb"  # never hijacks someone else's pool


def test_existing_pools_parses_zpool_output(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        if argv[:2] == ["zpool", "list"] and "-v" not in argv:
            return SimpleNamespace(returncode=0, stdout="bench-pool\t53687091200\n")
        if argv[:2] == ["zpool", "list"]:
            return SimpleNamespace(returncode=0, stdout="bench-pool\t49.5G\t26M\t49.5G\t-\t-\t0%\t0%\t1.00x\tONLINE\t-\n\t/dev/nvme1n1p1\t50.0G\t26M\t49.5G\t-\t-\t0%\t0.05%\t-\tONLINE\n")
        if argv[:2] == ["zfs", "list"]:
            return SimpleNamespace(returncode=0, stdout="bench-pool\t/bench-pool\nbench-pool/mariadb\t/var/lib/mysql\n")
        if argv[0] == "lsblk":
            return SimpleNamespace(returncode=0, stdout="nvme1n1\n")
        raise AssertionError(f"unexpected command {argv}")

    monkeypatch.setattr(volume_manager.subprocess, "run", fake_run)
    pools = volume_manager.existing_pools()
    assert pools == [
        PoolInfo(
            name="bench-pool",
            size_bytes=50 * G,
            device="/dev/nvme1n1",
            datasets=[
                DatasetInfo(name="bench-pool", mountpoint="/bench-pool"),
                DatasetInfo(name="bench-pool/mariadb", mountpoint="/var/lib/mysql"),
            ],
        )
    ]


def test_existing_pools_empty_when_zfs_missing(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        raise FileNotFoundError("zpool")

    monkeypatch.setattr(volume_manager.subprocess, "run", fake_run)
    assert volume_manager.existing_pools() == []


# ── single dataset setup + bind mounts ────────────────────────────────────────

from pathlib import Path
from unittest.mock import MagicMock

from pilot.exceptions import VolumeError
from pilot.managers.volume_manager import VolumeManager


def _fake_run_factory(calls, pool_exists=True, dataset_exists=False, is_mountpoint=False):
    def fake_run(cmd):
        calls.append(cmd)
        if cmd[:2] == ["zpool", "list"]:
            if pool_exists:
                return SimpleNamespace(stdout=b"bench-pool\n")
            raise VolumeError("no pool")
        if cmd[:2] == ["zfs", "list"]:
            if dataset_exists:
                return SimpleNamespace(stdout=b"bench-pool/shop\n")
            raise VolumeError("no dataset")
        if cmd[:1] == ["mountpoint"]:
            if is_mountpoint:
                return SimpleNamespace(stdout=b"")
            raise VolumeError("not a mountpoint")
        return SimpleNamespace(stdout=b"")
    return fake_run


def test_setup_reuses_existing_pool_and_creates_dataset(monkeypatch) -> None:
    monkeypatch.setattr(volume_manager.shutil, "which", lambda _, **kw: "/usr/sbin/zfs")
    config = VolumeConfig(enabled=True, pool="bench-pool", name="shop", backing="device", device="/dev/sdb")
    mgr = VolumeManager(config)
    calls: list[list[str]] = []
    monkeypatch.setattr(VolumeManager, "_run", lambda self, cmd: _fake_run_factory(calls)(cmd))

    mgr.setup()

    assert not any(c[:3] == ["sudo", "zpool", "create"] for c in calls)  # pool reused
    assert ["sudo", "zfs", "create", "bench-pool/shop"] in calls  # dataset created
    assert ["sudo", "zfs", "set", "recordsize=16K", "bench-pool/shop"] in calls


def test_bind_mount_skips_when_already_mounted(monkeypatch) -> None:
    mgr = VolumeManager(VolumeConfig())
    calls: list[list[str]] = []
    monkeypatch.setattr(VolumeManager, "_run", lambda self, cmd: _fake_run_factory(calls, is_mountpoint=True)(cmd))

    mgr.bind_mount(Path("/src"), Path("/dst"))

    assert not any(c[:3] == ["sudo", "mount", "--bind"] for c in calls)


def test_bind_mount_mounts_when_absent(monkeypatch) -> None:
    mgr = VolumeManager(VolumeConfig())
    calls: list[list[str]] = []
    monkeypatch.setattr(VolumeManager, "_run", lambda self, cmd: _fake_run_factory(calls, is_mountpoint=False)(cmd))

    mgr.bind_mount(Path("/src"), Path("/dst"))

    assert ["sudo", "mount", "--bind", "/src", "/dst"] in calls


def test_persist_bind_mount_skips_when_already_in_fstab(monkeypatch) -> None:
    mgr = VolumeManager(VolumeConfig())
    monkeypatch.setattr(VolumeManager, "_fstab_has_target", lambda self, target: True)
    run = MagicMock()
    monkeypatch.setattr(volume_manager.subprocess, "run", run)

    mgr.persist_bind_mount(Path("/src"), Path("/dst"))

    run.assert_not_called()


def test_persist_bind_mount_writes_ordered_entry(monkeypatch) -> None:
    mgr = VolumeManager(VolumeConfig())
    monkeypatch.setattr(VolumeManager, "_fstab_has_target", lambda self, target: False)
    run = MagicMock()
    monkeypatch.setattr(volume_manager.subprocess, "run", run)

    mgr.persist_bind_mount(Path("/src"), Path("/dst"))

    run.assert_called_once()
    written = run.call_args.kwargs["input"]
    assert b"/src /dst none bind" in written
    assert b"x-systemd.requires=zfs-mount.service" in written


# ── Alpine ZFS install ──────────────────────────────────────────────────────────


def _vm() -> VolumeManager:
    return VolumeManager(VolumeConfig())


def test_alpine_kernel_flavor(monkeypatch) -> None:
    monkeypatch.setattr(os, "uname", lambda: SimpleNamespace(release="6.6.41-0-lts"))
    assert VolumeManager._alpine_kernel_flavor() == "lts"
    monkeypatch.setattr(os, "uname", lambda: SimpleNamespace(release="6.6.41-0-virt"))
    assert VolumeManager._alpine_kernel_flavor() == "virt"
    # A non-Alpine kernel (no flavor suffix) yields "" — no matching zfs-<flavor>.
    monkeypatch.setattr(os, "uname", lambda: SimpleNamespace(release="6.1.155+"))
    assert VolumeManager._alpine_kernel_flavor() == ""


def _patch_zfs_install(monkeypatch, *, zpool_rc: int, installed: list) -> None:
    pkg = SimpleNamespace(install=lambda *p: installed.extend(p))
    monkeypatch.setattr(volume_manager, "get_package_manager", lambda: pkg)
    monkeypatch.setattr(volume_manager, "service_enable_command", lambda s: ["rc-update", "add", s])
    monkeypatch.setattr(volume_manager, "_privileged", lambda c: c)
    monkeypatch.setattr(volume_manager.shutil, "which", lambda n, **kw: "/usr/sbin/zfs")
    monkeypatch.setattr(
        volume_manager.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=zpool_rc, stdout=b"", stderr=b""),
    )


def test_install_zfs_alpine_uses_apk_packages_and_raises_without_module(monkeypatch) -> None:
    """Installs Alpine's zfs/zfs-openrc (not Debian's zfsutils-linux); without a
    loadable module it fails with a clear, actionable error rather than crashing
    on a missing apk package."""
    monkeypatch.setattr(os, "uname", lambda: SimpleNamespace(release="6.1.155+"))
    installed: list = []
    _patch_zfs_install(monkeypatch, zpool_rc=1, installed=installed)
    with pytest.raises(VolumeError, match="ZFS kernel module"):
        _vm()._install_zfs_alpine()
    assert "zfs" in installed and "zfs-openrc" in installed


def test_install_zfs_alpine_succeeds_when_module_loads(monkeypatch) -> None:
    monkeypatch.setattr(os, "uname", lambda: SimpleNamespace(release="6.6.41-0-virt"))
    installed: list = []
    _patch_zfs_install(monkeypatch, zpool_rc=0, installed=installed)
    _vm()._install_zfs_alpine()  # zpool reachable → no raise
    assert "zfs-virt" in installed  # the matching kernel-module package


def test_ensure_zfs_routes_to_alpine(monkeypatch) -> None:
    monkeypatch.setattr(volume_manager, "is_alpine", lambda: True)
    monkeypatch.setattr(volume_manager.shutil, "which", lambda n, **kw: None)
    called: dict = {}
    monkeypatch.setattr(VolumeManager, "_install_zfs_alpine", lambda self: called.setdefault("alpine", True))
    _vm()._ensure_zfs()
    assert called.get("alpine")
