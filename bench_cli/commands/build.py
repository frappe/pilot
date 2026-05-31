from __future__ import annotations

from bench_cli.core.bench import Bench
from bench_cli.utils import run_command


class BuildCommand:
    def __init__(self, bench: Bench) -> None:
        self.bench = bench

    def run(self) -> None:
        run_command(
            [*self.bench.frappe_call, "frappe", "build", "--force"],
            cwd=self.bench.sites_path,
            stream_output=True,
        )
