from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class CliContext:
    installation_root: Path
    bench_name: str | None = None
    verbose: bool = False
    assume_yes: bool = False

    @property
    def all_benches(self) -> bool:
        return self.bench_name == "all"

    def for_bench(self, name: str) -> "CliContext":
        return replace(self, bench_name=name)
