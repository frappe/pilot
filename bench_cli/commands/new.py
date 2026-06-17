import argparse
import secrets
import socket
from pathlib import Path

from bench_cli.commands.base import Command
from bench_cli.exceptions import BenchError
from bench_cli.platform import is_linux
from bench_cli.utils import iter_sibling_benches


class NewCommand(Command):
    name = "new"
    help = "Create a new bench."
    requires_bench = False

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("name", help="Name for the new bench.")

    @classmethod
    def from_args(cls, args, bench):
        from bench_cli.loader import cli_root

        return cls(cli_root() / "benches" / args.name, args.name)

    def __init__(self, target_directory: Path, name: str) -> None:
        self.target_directory = target_directory
        self.name = name

    def run(self) -> None:
        from bench_cli.config.bench_toml_builder import BenchTomlBuilder, default_ports

        bench_toml = self.target_directory / "bench.toml"
        if bench_toml.exists():
            raise BenchError(f"Bench '{self.name}' already exists.")

        benches_dir = self.target_directory.parent
        if not benches_dir.exists():
            print(f"Creating benches directory at {benches_dir}")
            benches_dir.mkdir(parents=True, exist_ok=True)

        print(f"Creating bench directory: {self.target_directory}")
        self.target_directory.mkdir(parents=True, exist_ok=True)

        offset = self._pick_port_offset(self.target_directory)
        print("Writing bench.toml")
        settings = {"admin_password": secrets.token_hex(nbytes=5)}
        # New benches get their own MariaDB instance (mariadb@<name>) with an
        # isolated socket/datadir; mariadb.port is offset automatically via
        # _PORT_FIELDS. Existing benches without these fields keep using the
        # shared system MariaDB. macOS is dev-only (Homebrew, no systemd
        # template units), so it stays on the shared server.
        if is_linux():
            settings.update(
                {
                    "mariadb_instance": self.name,
                    "mariadb_socket_path": f"/run/mysqld/mysqld-{self.name}.sock",
                    "mariadb_data_dir": f"/var/lib/mysql-{self.name}",
                }
            )
        bench_toml.write_text(BenchTomlBuilder(self.name, settings, port_offset=offset).render())

        admin_port = default_ports()["admin.port"] + offset
        print(f"\nBench '{self.name}' created at {self.target_directory}")
        print("\nNext step:")
        print("  bench start")
        print(f"  Open http://localhost:{admin_port} — the setup wizard guides you through the rest,")

    def _pick_port_offset(self, bench_path: Path) -> int:
        """Smallest offset (added to every base port) that collides with
        neither another bench's bench.toml nor a port that's actually live
        right now — covers both stale configs and orphaned processes."""
        from bench_cli.config.bench_toml_builder import default_ports

        bases = default_ports()
        base_http_port = bases["http_port"]
        used = set()

        for _, config in iter_sibling_benches(bench_path):
            try:
                used.add(config.http_port - base_http_port)
            except Exception:
                continue

        offset = 0
        while offset in used or any(self._port_is_live(base + offset) for base in bases.values()):
            offset += 1
        return offset

    @staticmethod
    def _port_is_live(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            return False
