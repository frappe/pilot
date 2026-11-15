from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar

from pilot.commands import Arg, BenchMode, Command
from pilot.core.bench.diagnostics import BenchDiagnostics
from pilot.exceptions import BenchError


@dataclass(kw_only=True)
class DoctorCommand(Command):
    name: ClassVar[str] = "doctor"
    help: ClassVar[str] = "Check bench configuration and runtime protocol health without changing data."
    bench_mode: ClassVar[BenchMode] = BenchMode.PATH

    path: Annotated[Path | None, Arg(help="Bench directory, including one with broken configuration.")] = None
    json: Annotated[bool, Arg(help="Print a versioned JSON report.")] = False
    offline: Annotated[bool, Arg(help="Inspect local files only; skip runtime protocol probes.")] = False
    timeout: Annotated[float, Arg(help="Whole-probe deadline in seconds (0.1-30; default 2).")] = 2.0

    def run(self) -> None:
        path = self.path or self.bench_path
        if path is None:
            raise BenchError("No bench selected. Pass --path or run doctor through the CLI.")
        result = BenchDiagnostics(path, offline=self.offline, timeout=self.timeout).run()
        if self.json:
            self.report(json.dumps(result.to_dict(), indent=2))
        else:
            self.report(f"Bench checks ({result.mode}): {result.path}")
            for check in result.checks:
                self.report(f"{check.status.upper()} {check.code} [{check.target}]: {check.message}")
                if check.reason:
                    self.report(f"      Reason: {check.reason}")
                if check.fix:
                    self.report(f"      Fix: {check.fix}")
            self.report(
                "Summary: "
                + ", ".join(f"{count} {status}" for status, count in result.to_dict()["summary"].items())
            )
            self.report(
                "Runtime probes cover database queries, Redis PING, and local HTTP; worker and scheduler health is not checked."
            )
        if result.has_errors:
            raise SystemExit(1)
        if result.has_unavailable:
            raise SystemExit(2)
