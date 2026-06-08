from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

from bench_cli.exceptions import BenchError

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class FrappeCommand:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def run(self, args: tuple[str, ...]) -> None:
        self.run_raw(["frappe", *args])

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
