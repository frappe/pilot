import argparse
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
        parser.add_argument(
            "--admin-domain",
            default="",
            help="Admin domain for this bench. Optional for development; "
                 "required by 'bench setup production' (pass it there if omitted here).",
        )

    @classmethod
    def from_args(cls, args, bench):
        from bench_cli.loader import cli_root

        return cls(
            cli_root() / "benches" / args.name,
            args.name,
            admin_domain=args.admin_domain,
        )

    def __init__(self, target_directory: Path, name: str, process_manager: str = "",
                 admin_domain: str = "", admin_tls: bool | None = None) -> None:
        self.target_directory = target_directory
        self.name = name
        self.process_manager = process_manager
        self.admin_domain = admin_domain
        # None → inherit the server-wide value from a sibling bench (default False).
        self.admin_tls = admin_tls

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
        admin_tls = self.admin_tls if self.admin_tls is not None else self._sibling_admin_tls()
        # admin.domain is left empty unless given: development serves the admin on
        # localhost, and 'bench setup production' requires a real domain (via its
        # --admin-domain flag or here), erroring rather than deploying a placeholder.
        settings = {
            "admin_enabled": True,
            "admin_domain": self.admin_domain,
            "admin_tls": admin_tls,
        }
        if self.process_manager:
            settings["production_process_manager"] = self.process_manager
        # The Let's Encrypt account email is a server-wide setting; inherit it
        # from a sibling bench so a new production bench can issue certs without
        # re-entering it.
        sibling_email = self._sibling_letsencrypt_email()
        if sibling_email:
            settings["letsencrypt_email"] = sibling_email
        # New benches get their own MariaDB instance with an isolated
        # socket/datadir; mariadb.port is offset automatically via _PORT_FIELDS.
        # Linux uses a per-bench instance (systemd mariadb@<name>, or a generated
        # OpenRC mariadb-<name> on Alpine). macOS (Homebrew) has no per-instance
        # mechanism, so it stays on the shared server.
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
        print(f"  Then open http://localhost:{admin_port} — the setup wizard takes it from there.")

    def _sibling_letsencrypt_email(self) -> str:
        """The Let's Encrypt email from any sibling bench that has one, so a new
        production bench inherits the server-wide ACME account."""
        for _, config in iter_sibling_benches(self.target_directory):
            email = getattr(config.letsencrypt, "email", "")
            if email:
                return email
        return ""

    def _sibling_admin_tls(self) -> bool:
        """Carry forward the server-wide TLS choice from a sibling bench; default
        to False (plain HTTP, enable TLS explicitly) when this is the first bench."""
        for _, config in iter_sibling_benches(self.target_directory):
            return bool(getattr(config.admin, "tls", False))
        return False

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
