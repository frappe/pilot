import argparse
import secrets
import socket
import tomllib
from pathlib import Path

from bench_cli.commands.base import Command
from bench_cli.config.bench_toml_builder import BenchTomlBuilder, default_ports
from bench_cli.exceptions import BenchError


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
        bench_toml = self.target_directory / "bench.toml"
        if bench_toml.exists():
            raise BenchError(f"A bench named '{self.name}' already exists at {self.target_directory}. Choose a different name or remove the existing bench.")

        benches_dir = self.target_directory.parent
        if not benches_dir.exists():
            print(f"Creating benches directory at {benches_dir}")
            benches_dir.mkdir(parents=True, exist_ok=True)

        print(f"Creating bench directory: {self.target_directory}")
        self.target_directory.mkdir(parents=True, exist_ok=True)

        offset = self._pick_port_offset(benches_dir)
        print("Writing bench.toml")
        settings = {"admin_password": secrets.token_hex(nbytes=5)}
        bench_toml.write_text(BenchTomlBuilder(self.name, settings, port_offset=offset).render())

        admin_port = default_ports()["admin.port"] + offset
        print(f"\nBench '{self.name}' created at {self.target_directory}")
        print("\nNext step:")
        print("  bench start")
        print(f"  Open http://localhost:{admin_port} — the setup wizard guides you through the rest,")

    def _pick_port_offset(self, benches_dir: Path) -> int:
        """Smallest offset (added to every base port) that collides with
        neither another bench's bench.toml nor a port that's actually live
        right now — covers both stale configs and orphaned processes."""
        bases = default_ports()
        base_http_port = bases["http_port"]

        used = set()
        if benches_dir.is_dir():
            for other in benches_dir.iterdir():
                toml_path = other / "bench.toml"
                if not toml_path.exists():
                    continue
                try:
                    with open(toml_path, "rb") as f:
                        data = tomllib.load(f)
                    used.add(data.get("bench", {}).get("http_port", base_http_port) - base_http_port)
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
