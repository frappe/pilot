# Volume Management

bench manages all storage on ZFS. On Linux every bench runs on ZFS — volume setup is a mandatory part of `bench init` (macOS is dev-only and skips it).

**One shared pool, one dataset per bench.** A single ZFS pool can host many benches: each bench gets **one dataset** named after it (`<pool>/<bench>`). That single dataset holds **both** the bench files and the bench's MariaDB data, exposed at their conventional paths via **bind mounts**. Because everything for a bench lives on one dataset, a snapshot or rollback is **atomic across the bench files and the database**.

The pool can be backed three ways, selected by `volume.backing`:

- **`backing = "device"`** — a dedicated block device (`/dev/sdb`). Best performance; use this when a spare disk or attached volume is available.
- **`backing = "image"`** — a preallocated image file on the root filesystem (default `/var/lib/bench-zfs/<pool>.img`), used as a file vdev. For machines **without a spare disk**: everything above the vdev (the dataset, quota, reservation, snapshots) works identically. Slightly lower performance than a dedicated device since ZFS sits on top of the existing filesystem — fine for dev and small setups.
- **`backing = "auto"`** — let `bench init` decide. An unused disk is auto-discovered and used as device backing; if none exists, image backing is used. The quota and reservation are derived from the backing size. See [Auto backing](#auto-backing-discovery-and-smart-sizing) below.

The pool is **reused if it already exists** — `bench init` only ever *creates the dataset* inside it (the pool is created only when missing). This is what lets several benches share one pool.

The image file is always **preallocated** with `fallocate` (never sparse), so the pool can't be corrupted later by the root filesystem filling up — setup fails fast instead if the space isn't there.

---

## Bind mounts

The dataset mounts at its natural ZFS mountpoint (e.g. `/<pool>/<bench>`). Inside it are two subdirectories, each bind-mounted onto the path the rest of the system expects:

| Subdir | Bind-mounted onto | Holds |
|---|---|---|
| `<mount>/benches` | the bench directory (`bench-cli/benches/<bench>`) | apps, sites, env, logs, config |
| `<mount>/mariadb` | the MariaDB datadir (`/var/lib/mysql-<instance>`) | the bench's database |

The bind mounts are recorded in `/etc/fstab` so they survive reboots, ordered after `zfs-mount.service` (`x-systemd.requires=zfs-mount.service,x-systemd.after=zfs-mount.service`) so the dataset is mounted before the bind mounts attach. This keeps MariaDB's datadir and the bench directory at their conventional paths while the bytes physically live on the single dataset.

> **Per-bench MariaDB instances.** Each bench runs its own `mariadb@<instance>` (the `bench new` default on Linux) with datadir `/var/lib/mysql-<instance>`. The bind mount targets that instance datadir — see [Per-bench MariaDB instances](architecture.md#per-bench-mariadb-instances). This, combined with the single dataset, is what makes snapshots and rollbacks bench-independent.

---

## Auto backing — discovery and smart sizing

The minimal hands-off config:

```toml
[volume]
pool = "bench-pool"
backing = "auto"
```

During `bench init`, the volume setup step resolves `auto` to a concrete backing and **persists the resolved values back to `bench.toml`**, so the settings UI and re-runs see real values.

**Device discovery** (`lsblk -J -b`): a disk qualifies as *unused* when it is a whole disk (`type = disk`), writable, has **no partitions**, **no filesystem signature** (excludes `zfs_member`, `LVM2_member`, `linux_raid_member`, ext4, ...), **nothing mounted**, and is at least 10G. The largest qualifying disk wins. A disk already hosting the bench pool is preferred (the pool is reused).

**Smart sizing** (also used by the setup wizard's prefilled defaults):

| Value | Default |
|---|---|
| Image size (no spare disk) | 75% of rootfs free space, min 10G |
| `dataset.quota` | 100% of backing size |
| `dataset.reservation` | 15% of backing size |

By default a single bench's dataset may grow to fill the backing. **To host multiple benches in one pool, lower the quota** so each dataset is capped and the pool can be carved between them. All values are floored to whole gigabytes (min 1G).

> **Auto backing implies auto sizing.** With `backing = "auto"`, the quota and reservation are always recomputed from the resolved backing — set `backing = "device"` or `"image"` explicitly if you want manual control over sizes.

---

## Design constraints

- **Mandatory on Linux.** Every bench runs on ZFS — there is no off switch. Machines without a spare disk use a disk image on the root filesystem (`backing = "image"` or `"auto"`). macOS (dev only) skips volume setup entirely.
- **One shared pool, one dataset per bench.** A single pool on one backing (disk or image file) hosts a dataset per bench (`<pool>/<bench>`). Each dataset holds both the bench files and that bench's MariaDB data via bind mounts, so storage is shared and snapshots are atomic across files + database.
- **Reuse, don't recreate.** An existing pool is reused; only the per-bench dataset is created. Several benches can therefore share one pool.
- **Quota and reservation from bench.toml.** Space limits and guarantees are declared in `bench.toml` — no manual `zfs set` commands needed.
- **Global snapshots.** A snapshot captures the whole bench (files + DB) at once via `bench volume snapshot`; a rollback restores both together. Scheduling is left to the operator (cron, etc.).
- **Linux only.** ZFS volume management targets Ubuntu/Linux servers. `VolumeSetupCommand` exits with a clear error on macOS.
- **No pool destruction.** bench will never destroy a ZFS pool without an explicit user-confirmed command.

---

## bench.toml additions

```toml
# ── Volume (ZFS, mandatory on Linux) ────────────────────────────────────────────────
[volume]
pool = "bench-pool"        # ZFS pool name (created if missing, reused if present)
backing = "device"         # "device" (dedicated disk) | "image" (file on root FS) | "auto" (discover)
device = "/dev/sdb"        # block device to create the pool on (backing = "device")
                           # ignored if the pool already exists

[volume.image]             # only read when backing = "image"
size = "60G"               # preallocated size of the image file (fallocate)
# path = "/var/lib/bench-zfs/bench-pool.img"   # optional, this is the default

[volume.dataset]
reservation = "15G"        # guaranteed space for this bench (files + database)
quota = "60G"              # hard cap on this bench's space — lower it to fit more benches in the pool
```

The dataset is named after the bench (`<pool>/<bench-name>`); set `name` under `[volume]` only to override it.

### Validation

On every config load:
- `volume.pool` must be a non-empty string.
- `volume.backing` must be `"device"`, `"image"`, or `"auto"`.
- `backing = "auto"` → no other backing fields required; everything is resolved at `bench init` time.
- `backing = "device"` → `volume.device` is required.
- `backing = "image"` → `volume.image.size` is required (valid ZFS size); `volume.image.path`, if set, must be absolute. Before setup, the root filesystem must have enough free space to preallocate the image.
- All sizes (`reservation`, `quota`, `image.size`) must be positive integers with an optional `K`/`M`/`G`/`T` suffix (e.g. `"10G"`, `"512M"`) — no decimals, negatives, or zero.
- The reservation cannot exceed the quota, and neither may exceed the backing size (device size or image size).

---

## Config dataclasses

```python
@dataclass
class DatasetConfig:
    reservation: str = "5G"
    quota: str = "50G"

@dataclass
class VolumeConfig:
    pool: str = "bench-pool"
    name: str = ""          # dataset leaf, defaults to the bench name
    backing: str = "auto"   # "device" | "image" | "auto"
    device: str = ""
    image: ImageConfig = field(default_factory=ImageConfig)
    dataset: DatasetConfig = field(default_factory=DatasetConfig)

    @property
    def dataset_path(self) -> str:
        return f"{self.pool}/{self.name or 'bench'}"
```

`VolumeConfig` is part of `BenchConfig`, and `name` is populated from the bench name when the config is parsed.

---

## `VolumeManager`

All ZFS operations go through `VolumeManager`. It runs `zfs` and `zpool` as subprocesses — no Python ZFS library needed. ZFS is installed automatically via the system package manager if not already present.

Key methods:

- `create_pool()` — `zpool create <pool> <vdev>`, **skipped if the pool already exists** (reuse).
- `create_dataset(dataset)` / `set_quota` / `set_reservation` / `set_recordsize` — dataset lifecycle; idempotent.
- `setup()` — ensure ZFS, reuse-or-create the pool, create this bench's single dataset with its quota/reservation, and set `recordsize=16K` (the dataset holds the DB, whose 16K page size mismatches ZFS's 128K default and would otherwise cause IO amplification).
- `bind_mount(source, target)` — `mount --bind` the dataset subdir onto its conventional path; idempotent (skips if already a mountpoint).
- `persist_bind_mount(source, target)` — record the bind mount in `/etc/fstab`, ordered after `zfs-mount.service`; idempotent.
- `snapshot(dataset, tag)` / `list_snapshots(dataset)` / `rollback_snapshot(dataset, tag)` / `destroy_snapshot(dataset, tag)` — snapshot operations on the bench's single dataset.

```python
@dataclass
class SnapshotInfo:
    name: str          # e.g. "bench-pool/shop@20250528-140000"
    dataset: str       # "bench-pool/shop"
    snapshot_tag: str  # "20250528-140000"
    created_at: datetime
    used_bytes: int
```

---

## Integration with `bench init`

On Linux, `InitCommand` runs `VolumeSetupCommand` as step 3, immediately after installing system packages and before `Bench.create_directories()` (so all subsequent directory creation lands on the dataset) and before the MariaDB instance is provisioned (so its datadir is initialised straight onto the dataset).

```
1.  Validate bench.toml
2.  Install system packages
3.  [Linux] Set up ZFS volumes
      • manager.setup()          — reuse/create pool, create the bench's dataset
      • bind benches subdir      — migrate bench dir → mount --bind → fstab
      • bind mariadb subdir      — create mysql-owned subdir → mount --bind → fstab
4.  Provision MariaDB instance    ← initialises into the bound datadir (on the dataset)
5.  Create bench directory structure  ← runs on the dataset from here on
...
```

`SnapshotOrchestrator` coordinates the consistency guarantees: every snapshot quiesces MariaDB (`FLUSH TABLES WITH READ LOCK`) before `zfs snapshot`, and every rollback puts sites into maintenance mode and stops/starts MariaDB around `zfs rollback -r`.

---

## CLI commands

### `bench volume status`

```bash
bench volume status
```

```
Pool       bench-pool            ONLINE  size=100G  free=87G
Dataset    bench-pool/shop       quota=60G  reservation=15G  used=5.0G  avail=55G
```

### `bench volume snapshot`

Creates a timestamped snapshot of the whole bench (files + database).

```bash
bench volume snapshot
```

Snapshot tags are generated as `YYYYMMDD-HHMMSS`.

### `bench volume list-snapshots`

```bash
bench volume list-snapshots
```

```
Dataset: bench-pool/shop
  20250528-140000               created: 2025-05-28 14:00:00  used: 124M
  20250527-020000               created: 2025-05-27 02:00:00  used: 98M
```

### `bench volume destroy-snapshot` / `restore-snapshot`

```bash
bench volume destroy-snapshot 20250527-020000
bench volume restore-snapshot 20250527-020000   # sites → maintenance, MariaDB stopped, then rolled back
```

A restore rolls the bench back to the snapshot — **all data written since (both files and database) is lost**, and newer snapshots are destroyed. MariaDB is stopped and sites are put into maintenance mode for the duration.

---

## Live quota and reservation changes

The quota and reservation can be updated at any time via the **ZFS Volume** tab in the admin Settings modal — no bench restart required. The change is applied in two steps:

1. **Validate** — before writing `bench.toml`, the new quota is compared against the dataset's current used bytes (`zfs get -H -p -o value used <dataset>`). If the new quota would be less than the used size, the request is rejected and nothing is written.

   > Setting a quota below the current used size does not make ZFS refuse the command, but it immediately blocks all further writes to the dataset. MariaDB would receive "Got error 28 from storage engine" and crash. The validation step prevents this.

2. **Apply** — after `bench.toml` is written, `zfs set quota=<value>` and `zfs set reservation=<value>` are run for the dataset.

`_parse_size_bytes` handles suffixes `K`, `M`, `G`, `T`, `P` (base-1024) and bare integer strings.

---

## Error handling

`VolumeManager` raises `pilot.exceptions.VolumeError` (a subclass of `BenchError`) for all ZFS command failures. The CLI catches this at the top level and prints the error along with the underlying command that failed.

---

## Security notes

- All `zpool create`, `zfs create`, `zfs set`, `mount --bind`, `rsync`, and `/etc/fstab` writes that require elevated privileges run under `sudo`.
- ZFS dataset mounting is native; bind mounts are persisted in `/etc/fstab` ordered after `zfs-mount.service`.
- Dataset names and snapshot tags are constructed from `bench.toml` values and generated timestamps only. All ZFS calls use `subprocess` with a list argv — never `shell=True`.
