from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class GitCommand(Command):
    name: ClassVar[str] = "git"
    help: ClassVar[str] = (
        "Run git in an app's checkout with the bench's stored Git credential. "
        "Example: pilot -b NAME git frappe fetch origin."
    )

    app: Annotated[str, Arg(help="App folder under apps/.")]
    args: tuple[str, ...] = ()

    def run(self) -> None:
        """Run git in the app and exit with its code."""
        sys.exit(self.bench.app(self.app).run_git(*self.args).returncode)
