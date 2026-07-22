from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import Command
from pilot.exceptions import BenchError


@dataclass(kw_only=True)
class FrappeCommand(Command):
    name: ClassVar[str] = "frappe"
    help: ClassVar[str] = "Run a frappe CLI command."

    args: tuple[str, ...] = ()

    def run(self) -> None:
        python = self.bench.env_path / "bin" / "python"
        if not python.exists():
            raise BenchError("Frappe environment not found. Run 'bench init' first.")
        result = subprocess.run(
            [*self.bench.frappe_call, "frappe", *self.args],
            cwd=self.bench.sites_path,
        )
        sys.exit(result.returncode)
