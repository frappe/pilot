import argparse
import secrets
from pathlib import Path

from bench_cli.commands.base import Command
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
        from bench_cli.config.bench_toml_builder import BenchTomlBuilder

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
        settings = {
            "admin_password": secrets.token_hex(nbytes=5),
        }
        bench_toml.write_text(BenchTomlBuilder(self.name, settings).render())

        print(f"\nBench '{self.name}' created at {self.target_directory}")
        print("\nNext step:")
        print("  bench start")
        print("  Open http://localhost:8002 — the setup wizard guides you through the rest,")