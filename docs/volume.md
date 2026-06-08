# Volume Management

bench supports optional ZFS-based volume management for benches running on a dedicated disk. When enabled, a single ZFS pool is created on a block device, and separate datasets are carved out for bench data and MariaDB data — each with configurable quotas and reservations.

---

## Design constraints

- **Opt-in only.** ZFS volume management is disabled by default. Set `volume.enabled = true` in `bench.toml` to activate it.
- **One pool, two datasets.** A single ZFS pool on one large disk holds two datasets: one for bench directories (`<pool>/benches`) and one for MariaDB data (`<pool>/mariadb`). This keeps data locality simple and snapshotting independent per concern.
- **Quotas and reservations from bench.toml.** Space limits and guarantees are declared in `bench.toml` — no manual `zfs set` commands needed.
- **Snapshot support.** ZFS datasets can be snapshotted on demand via `bench volume snapshot`. This is a building block for backup workflows; scheduling is left to the operator (cron, etc.).
- **Linux only.** ZFS volume management targets Ubuntu/Linux servers. `VolumeSetupCommand` exits with a clear error on macOS.
- **No pool destruction.** bench will never destroy a ZFS pool or rollback a dataset without an explicit user-confirmed command. All destructive operations require `--yes`.
- **Runs once during `bench init`.** Volume setup is not idempotent by design. It runs as part of `bench init` when `volume.enabled = true` and is not intended to be re-run.

---

## bench.toml additions

```toml
# ── Volume (ZFS, optional) ────────────────────────────────────────────────
[volume]
enabled = false            # set to true to enable ZFS volume management
pool = "bench-pool"        # ZFS pool name (created if it does not exist)
device = "/dev/sdb"        # block device to create the pool on
                           # ignored if the pool already exists

[volume.benches]
reservation = "10G"        # guaranteed space for bench directories
quota = "50G"              # hard cap on bench directory space

[volume.mariadb]
reservation = "5G"         # guaranteed space for MariaDB data files
quota = "20G"              # hard cap on MariaDB data space
data_dir = "/var/lib/mysql" # path MariaDB reads/writes its data files
                            # bench remounts the dataset here via zfs set mountpoint

[volume.snapshots]
enabled = false            # set to true to allow `bench volume snapshot`
```

### Validation

If `volume.enabled = true`:
- `volume.pool` and `volume.device` must be non-empty strings.
- `volume.benches.reservation` and `volume.mariadb.reservation` must be valid ZFS size strings (e.g. `"10G"`, `"512M"`).
- `volume.benches.quota` must be greater than `volume.benches.reservation`; same for `volume.mariadb`.
- `volume.mariadb.data_dir` must be an absolute path.

---

## Package layout additions

```
bench_cli/
├── config/
│   └── volume_config.py      # VolumeConfig, BenchesDatasetConfig, MariaDBDatasetConfig
│
├── commands/
│   └── volume.py             # VolumeSetupCommand, VolumeStatusCommand,
│                             # VolumeSnapshotCommand, VolumeListSnapshotsCommand,
│                             # VolumeDestroySnapshotCommand
│
└── managers/
    └── volume_manager.py     # VolumeManager, SnapshotInfo
```

---

## Config dataclasses

```python
@dataclass
class BenchesDatasetConfig:
    reservation: str = "10G"
    quota: str = "50G"

@dataclass
class MariaDBDatasetConfig:
    reservation: str = "5G"
    quota: str = "20G"
    data_dir: str = "/var/lib/mysql"

@dataclass
class SnapshotConfig:
    enabled: bool = False

@dataclass
class VolumeConfig:
    enabled: bool = False
    pool: str = ""
    device: str = ""
    benches: BenchesDatasetConfig = field(default_factory=BenchesDatasetConfig)
    mariadb: MariaDBDatasetConfig = field(default_factory=MariaDBDatasetConfig)
    snapshots: SnapshotConfig = field(default_factory=SnapshotConfig)

    @property
    def benches_dataset(self) -> str:
        return f"{self.pool}/benches"

    @property
    def mariadb_dataset(self) -> str:
        return f"{self.pool}/mariadb"
```

`VolumeConfig` is added to `BenchConfig`:

```python
@dataclass
class BenchConfig:
    ...
    volume: VolumeConfig = field(default_factory=VolumeConfig)
```

---

## `VolumeManager`

All ZFS operations go through `VolumeManager`. It runs `zfs` and `zpool` as subprocesses — no Python ZFS library needed. ZFS is installed automatically via the system package manager if not already present.

```python
class VolumeManager:
    def __init__(self, config: VolumeConfig): ...

    # Pool lifecycle

    def pool_exists(self) -> bool: ...

    def create_pool(self) -> None:
        """zpool create <pool> <device> — skipped if pool already exists."""

    # Dataset lifecycle

    def dataset_exists(self, dataset: str) -> bool: ...

    def create_dataset(self, dataset: str) -> None:
        """zfs create <dataset> — skipped if already exists."""

    def set_quota(self, dataset: str, quota: str) -> None:
        """zfs set quota=<quota> <dataset>"""

    def set_reservation(self, dataset: str, reservation: str) -> None:
        """zfs set reservation=<reservation> <dataset>"""

    # Mount helpers

    def get_mountpoint(self, dataset: str) -> Path:
        """Return the current mountpoint via zfs get -H -o value mountpoint <dataset>."""

    def set_mountpoint(self, dataset: str, target: Path) -> None:
        """
        Remount the dataset at target via: zfs set mountpoint=<target> <dataset>
        Creates target directory if it does not exist.
        ZFS persists the mountpoint natively — no /etc/fstab entry needed.
        """

    # Data migration

    def migrate_data(self, source: Path, dataset: str) -> None:
        """
        rsync source → ZFS auto-mount, then remount the dataset at source.
        Used for directories owned by other users that cannot be renamed
        (e.g. /var/lib/mysql owned by the mysql system user).
        The original files remain on the root FS hidden under the ZFS overlay.
        """

    def migrate_dir(self, source: Path, dataset: str) -> None:
        """
        mv source → source.migration (same-FS rename, instant)
        zfs set mountpoint=source
        rsync source.migration → source
        rm -rf source.migration
        Used for directories we own and can freely rename and delete (e.g. benches/).
        Leaves zero leftover files on the root FS.
        """

    # Snapshots

    def snapshot(self, dataset: str, tag: str) -> None:
        """zfs snapshot <dataset>@<tag> — raises VolumeError if snapshots.enabled is False."""

    def list_snapshots(self, dataset: str) -> list[SnapshotInfo]:
        """zfs list -t snapshot — returns list of SnapshotInfo sorted oldest-first."""

    def destroy_snapshot(self, dataset: str, tag: str) -> None:
        """zfs destroy <dataset>@<tag> — raises VolumeError if snapshot does not exist."""

    # High-level setup

    def setup(self) -> None:
        """
        Create the pool and both datasets with their quotas and reservations.
        Does not migrate data or set mountpoints — that is handled by VolumeSetupCommand.
        """
```

```python
@dataclass
class SnapshotInfo:
    name: str          # e.g. "bench-pool/benches@20250528-140000"
    dataset: str       # "bench-pool/benches"
    snapshot_tag: str  # "20250528-140000"
    created_at: datetime
    used_bytes: int
```

---

## Data migration strategies

When `bench init` runs with `volume.enabled = true`, it must move existing data from the root filesystem into the ZFS datasets before mounting. Two strategies are used depending on who owns the directory:

### MariaDB — rsync + ZFS overlay (`migrate_data`)

`/var/lib/mysql` is owned by the `mysql` system user and is under an AppArmor profile that blocks `rename()` on the directory itself even as root. Instead:

1. `manager.setup()` creates the dataset; ZFS auto-mounts it at `/<pool>/mariadb`.
2. MariaDB is stopped (`systemctl stop mariadb`).
3. `rsync -a /var/lib/mysql/ /<pool>/mariadb/` copies all data file-by-file (root can read any file).
4. `zfs set mountpoint=/var/lib/mysql <pool>/mariadb` remounts the dataset at the original path.
5. MariaDB is started (`systemctl start mariadb`).

The original files under `/var/lib/mysql` remain on the root FS hidden under the ZFS overlay mount. This is an accepted trade-off: data is minimal during `bench init`, and the hidden files waste a negligible amount of root FS space.

We also need to ensure the recordsize during mariadb dataset creation is mariadb friendly for maximum efficiency, will do that in the coming PRs.

### Benches — move + rsync + cleanup (`migrate_dir`)

`bench_cli_root/benches/` is owned by the frappe user, so it can be freely renamed:

1. `mv benches/ benches.migration/` — instant same-filesystem rename.
2. `zfs set mountpoint=benches/ <pool>/benches` — ZFS mounts the dataset at the original path.
3. `rsync -a benches.migration/ benches/` — copies data into ZFS.
4. `rm -rf benches.migration/` — removes the backup, leaving zero leftover files on the root FS.

The ZFS dataset mounts at `bench_cli_root/benches/` — the exact path `find_bench_root()` already scans — so no symlinks or path changes are needed anywhere else in the CLI.

---

## Integration with `bench init`

When `volume.enabled = true`, `InitCommand` runs `VolumeSetupCommand` as step 3, immediately after installing system packages (which installs and starts MariaDB so its data directory exists) and before `Bench.create_directories()` (so all subsequent directory creation lands on ZFS).

```
1.  Validate bench.toml
2.  Install system packages          ← MariaDB installed and started here
3.  [if volume.enabled] Set up ZFS volumes
      • manager.setup()              — create pool + datasets
      • setup_mariadb()              — migrate_data + restart MariaDB
      • setup_benches()              — migrate_dir
4.  Create bench directory structure  ← runs on ZFS from this point
5.  Create Python virtualenv
...
```

---

## CLI commands

### `bench volume status`

Displays current pool and dataset state.

```bash
bench volume status
```

Output:

```
Pool       bench-pool            ONLINE  size=100G  free=87G
Dataset    bench-pool/benches    quota=50G  reservation=10G  used=3.2G  avail=46G
Dataset    bench-pool/mariadb    quota=20G  reservation=5G   used=1.8G  avail=18G
```

### `bench volume snapshot`

Creates a timestamped snapshot of one or both datasets.

```bash
bench volume snapshot                    # snapshot both datasets
bench volume snapshot --dataset benches  # snapshot bench data only
bench volume snapshot --dataset mariadb  # snapshot MariaDB data only
```

Snapshot tags are generated as `YYYYMMDD-HHMMSS`. Pre-condition: `volume.snapshots.enabled = true` in `bench.toml`.

### `bench volume list-snapshots`

Lists all snapshots for a dataset.

```bash
bench volume list-snapshots
bench volume list-snapshots --dataset benches
```

Output:

```
Dataset: bench-pool/benches
  20250528-140000               created: 2025-05-28 14:00:00  used: 124M
  20250527-020000               created: 2025-05-27 02:00:00  used: 98M
```

### `bench volume destroy-snapshot`

Destroys a named snapshot. Requires `--yes` to confirm.

```bash
bench volume destroy-snapshot 20250527-020000 --dataset benches --yes
```

---

## Error handling

`VolumeManager` raises `bench_cli.exceptions.VolumeError` (a subclass of `BenchError`) for all ZFS command failures. The CLI catches this at the top level and prints the error along with the underlying command that failed.

Common errors:

| Situation | Message shown |
|-----------|--------------|
| `zfs` / `zpool` not installed | ZFS is installed automatically; if installation fails, a `VolumeError` is raised |
| Pool does not exist | Raised by `zpool list` inside `pool_exists()` |
| Dataset does not exist | Raised by `zfs list` inside `dataset_exists()` |
| Quota less than reservation | Caught at validation time before any ZFS commands run |
| `snapshots.enabled = false` | "Snapshots are disabled. Set volume.snapshots.enabled = true in bench.toml to enable." |
| Snapshot does not exist | "Snapshot '<dataset>@<tag>' does not exist." |

---

## Security notes

- All `zpool create`, `zfs create`, `zfs set`, `rsync`, `mv`, and `rm` operations that require elevated privileges run under `sudo`.
- Mounting is done via `zfs set mountpoint=` — ZFS handles persistence natively with no `/etc/fstab` modifications.
- `bench volume destroy-snapshot` always requires `--yes`. No snapshot is ever destroyed silently.
- Dataset names and snapshot tags are constructed from `bench.toml` values and generated timestamps only. All ZFS calls use `subprocess` with a list argv — never `shell=True`.
