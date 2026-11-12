from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar

from pilot.commands import Arg, BenchMode, Command


@dataclass(kw_only=True)
class ApiReferenceCommand(Command):
    name: ClassVar[str] = "api-reference"
    group: ClassVar[str] = "admin"
    help: ClassVar[str] = "Write the Admin API reference from the routes."
    bench_mode: ClassVar[BenchMode] = BenchMode.NONE

    output: Annotated[
        str | None, Arg(help="Markdown file to write (default: docs/admin-api-reference.md).")
    ] = None

    def run(self) -> None:
        from pilot.managers.environment import AdminEnvManager
        from pilot.utils import cli_root, run_command

        output = (
            Path(self.output).resolve() if self.output else cli_root() / "docs" / "admin-api-reference.md"
        )
        # The routes need Flask, which only the admin venv has.
        admin = AdminEnvManager(cli_root())
        admin.install_python_deps()
        run_command([str(admin.python), "-m", "admin.backend.api.reference", str(output)], cwd=cli_root())
        self.report(f"Wrote {output}")
