from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar

from pilot.commands import Arg, BenchMode, Command
from pilot.core.bench.diagnostics import BenchDiagnostics


@dataclass(kw_only=True)
class DoctorCommand(Command):
    name: ClassVar[str] = "doctor"
    help: ClassVar[str] = "Check local bench files and configuration without changing them."
    bench_mode: ClassVar[BenchMode] = BenchMode.NONE
    supports_bench_selection: ClassVar[bool] = False

    path: Annotated[Path | None, Arg(help="Bench directory, including one with broken configuration.")] = None
    json: Annotated[bool, Arg(help="Print a versioned JSON report.")] = False

    def run(self) -> None:
        path = self.path
        if path is None:
            current = Path.cwd()
            path = next(
                (parent for parent in (current, *current.parents) if (parent / "bench.toml").exists()),
                current,
            )
        result = BenchDiagnostics(path).run()
        if self.json:
            self.report(json.dumps(result.to_dict(), indent=2))
        else:
            self.report(f"Local checks: {result.path}")
            for check in result.checks:
                self.report(f"{check.status.upper():5} {check.code}: {check.message}\n      {check.path}")
                if check.fix:
                    self.report(f"      Fix: {check.fix}")
            self.report("Checks found errors." if result.has_errors else "Local checks passed.")
            self.report(
                "Checks cover files and configuration, not running services or database connectivity."
            )
        if result.has_errors:
            raise SystemExit(1)
