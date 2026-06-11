import json
from types import SimpleNamespace

import bench_cli.managers.volume_manager as volume_manager
from bench_cli.config.volume_config import VolumeConfig
from bench_cli.managers.volume_manager import (
    DiskInfo,
    PoolInfo,
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


def test_smart_dataset_sizes_strict_60_40_split() -> None:
    sizes = smart_dataset_sizes(100 * G)
    assert sizes == {
        "volume_benches_quota": "60G",
        "volume_mariadb_quota": "40G",
        "volume_benches_reservation": "10G",
        "volume_mariadb_reservation": "5G",
    }


def test_smart_dataset_sizes_floor_one_gigabyte() -> None:
    sizes = smart_dataset_sizes(10 * G)
    assert sizes["volume_mariadb_reservation"] == "1G"  # 5% of 10G = 0.5G -> floored to min 1G


def test_compute_smart_defaults_prefers_device(monkeypatch) -> None:
    monkeypatch.setattr(volume_manager, "existing_pools", lambda: [])
    monkeypatch.setattr(volume_manager, "discover_unused_disks", lambda: [DiskInfo("/dev/sdb", 100 * G)])
    defaults = compute_smart_defaults()
    assert defaults["volume_backing"] == "device"
    assert defaults["volume_device"] == "/dev/sdb"
    assert defaults["volume_benches_quota"] == "60G"
    assert defaults["available_devices"] == [{"path": "/dev/sdb", "size_bytes": 100 * G, "has_signature": False}]


def test_compute_smart_defaults_falls_back_to_image(monkeypatch) -> None:
    monkeypatch.setattr(volume_manager, "existing_pools", lambda: [])
    monkeypatch.setattr(volume_manager, "discover_unused_disks", lambda: [])
    monkeypatch.setattr(volume_manager, "default_image_size_bytes", lambda: 40 * G)
    defaults = compute_smart_defaults()
    assert defaults["volume_backing"] == "image"
    assert defaults["volume_image_size"] == "40G"
    assert defaults["volume_benches_quota"] == "24G"
    assert defaults["volume_mariadb_quota"] == "16G"
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
    assert config.benches.quota == "120G"
    assert config.mariadb.quota == "80G"
    assert config.benches.reservation == "20G"
    assert config.mariadb.reservation == "10G"


def test_resolve_auto_falls_back_to_image(monkeypatch) -> None:
    monkeypatch.setattr(volume_manager, "existing_pools", lambda: [])
    monkeypatch.setattr(volume_manager, "discover_unused_disks", lambda: [])
    monkeypatch.setattr(volume_manager, "default_image_size_bytes", lambda: 40 * G)
    config = VolumeConfig(pool="bench-pool", backing="auto")
    choice = resolve_auto_backing(config)
    assert "image" in choice
    assert config.backing == "image"
    assert config.image.size == "40G"
    assert config.benches.quota == "24G"
    assert config.mariadb.quota == "16G"


def test_resolve_auto_noop_for_explicit_backing() -> None:
    config = VolumeConfig(pool="bench-pool", backing="device", device="/dev/sdb")
    assert resolve_auto_backing(config) == ""
    assert config.device == "/dev/sdb"
    assert config.benches.quota == "50G"  # untouched defaults


# ── existing pool reuse ───────────────────────────────────────────────────────


def test_compute_smart_defaults_prefers_existing_pool(monkeypatch) -> None:
    monkeypatch.setattr(volume_manager, "existing_pools", lambda: [PoolInfo("bench-pool", 50 * G, "/dev/nvme1n1")])
    monkeypatch.setattr(volume_manager, "discover_unused_disks", lambda: [DiskInfo("/dev/sdb", 100 * G)])
    defaults = compute_smart_defaults()
    assert defaults["volume_backing"] == "device"
    assert defaults["volume_device"] == "/dev/nvme1n1"
    assert defaults["volume_pool"] == "bench-pool"
    assert defaults["volume_benches_quota"] == "30G"  # 60% of the pool, not the unused disk
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
    assert config.benches.quota == "30G"


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
        if argv[0] == "lsblk":
            return SimpleNamespace(returncode=0, stdout="nvme1n1\n")
        raise AssertionError(f"unexpected command {argv}")

    monkeypatch.setattr(volume_manager.subprocess, "run", fake_run)
    pools = volume_manager.existing_pools()
    assert pools == [PoolInfo(name="bench-pool", size_bytes=50 * G, device="/dev/nvme1n1")]


def test_existing_pools_empty_when_zfs_missing(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        raise FileNotFoundError("zpool")

    monkeypatch.setattr(volume_manager.subprocess, "run", fake_run)
    assert volume_manager.existing_pools() == []
