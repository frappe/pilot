from __future__ import annotations

import argparse
import subprocess
import sys
from typing import TYPE_CHECKING

from bench_cli.commands.base import Command
from bench_cli.exceptions import BenchError

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class FrappeCommand(Command):
    name = "frappe"
    help = "Run a frappe CLI command."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("args", nargs=argparse.REMAINDER)

    @classmethod
    def from_args(cls, args, bench):
        cmd = cls(bench)
        cmd._args = tuple(args.args)
        return cmd

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self._args: tuple[str, ...] = ()

    def run(self) -> None:
        self.run_raw(["frappe", *self._args])

    def run_raw(self, args: list[str] | tuple[str, ...]) -> None:
        python = self.bench.env_path / "bin" / "python"
        if not python.exists():
            raise BenchError(
                "Frappe environment not found. Run 'bench init' first."
            )
        result = subprocess.run(
            [*self.bench.frappe_call, *args],
            cwd=self.bench.sites_path,
        )
        sys.exit(result.returncode)
