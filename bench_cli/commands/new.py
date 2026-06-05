import secrets
from pathlib import Path

from bench_cli.exceptions import BenchError

_BENCH_TOML_TEMPLATE = """\
[bench]
name = "{name}"
python = "3.14"

[[apps]]
name = "frappe"
repo = "https://github.com/frappe/frappe"
branch = "version-16"

[mariadb]
host = "localhost"
port = 3306
root_password = "root"
# version = "10.6"

[redis]
port = 13000
# or use separate ports:
# cache_port = 13000
# queue_port = 11000
# socketio_port = 12000

[workers]
default = 2
short = 1
long = 1

# [[workers.custom]]
# queue = "backup"
# count = 2
# timeout = 3600

[admin]
port = 8002
enabled = false
timeout = 180
password = "{admin_password}"

# ── Production (optional) ─────────────────────────────────────────────────
# Uncomment to enable production mode (nginx + process manager).
# Without this section, 'bench start' uses Procfile-based dev mode.
#
# [production]
# nginx = false          # enable nginx reverse proxy
# lightweight = true   # true = systemd (lower memory), false = supervisor

# ── Volume (ZFS, optional) ────────────────────────────────────────────────
# Uncomment and configure to use ZFS-based volume management.
# Requires Linux and the zfsutils-linux package.
#
# [volume]
# enabled = false
# pool = "bench-pool"       # ZFS pool name (created on first bench init)
# device = "/dev/sdb"       # block device to use for the pool
#
# [volume.benches]
# reservation = "10G"       # guaranteed space for bench directories
# quota = "50G"             # hard cap on bench directory space
#
# [volume.mariadb]
# reservation = "5G"        # guaranteed space for MariaDB data files
# quota = "20G"             # hard cap on MariaDB data space
# data_dir = "/var/lib/mysql"
#
# [volume.snapshots]
# enabled = false           # set to true to enable bench volume snapshot
"""


class NewCommand:
    def __init__(self, target_directory: Path, name: str) -> None:
        self.target_directory = target_directory
        self.name = name

    def run(self) -> None:
        bench_toml = self.target_directory / "bench.toml"
        if bench_toml.exists():
            raise BenchError(f"A bench named '{self.name}' already exists at {self.target_directory}. Choose a different name or remove the existing bench.")

        benches_dir = self.target_directory.parent
        if not benches_dir.exists():
            print(f"Creating benches directory at {benches_dir}")
            benches_dir.mkdir(parents=True, exist_ok=True)

        print(f"Creating bench directory: {self.target_directory}")
        self.target_directory.mkdir(parents=True, exist_ok=True)

        print("Writing bench.toml")
        bench_toml.write_text(_BENCH_TOML_TEMPLATE.format(name=self.name, admin_password=secrets.token_hex(nbytes=5)))

        print(f"\nBench '{self.name}' created at {self.target_directory}")
        print("\nNext steps:")
        print(f"  1. Edit the config:  {bench_toml}")
        print("  2. Run:              bench init")
        print("  3. Create a site:    bench new-site site1.localhost")
        print()
        print("ZFS volume management (optional):")
        print("  If you have a spare block device, uncomment the [volume] section in bench.toml")
        print("  and choose a ZFS preset (pool, device, quotas) before running bench init.")
        print("  Volume setup runs as part of bench init and cannot be configured after that.")
        print("  Skip this entirely if no dedicated storage device is available.")
