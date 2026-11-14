from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import Command


@dataclass(kw_only=True)
class SetupLetsEncryptCommand(Command):
    name: ClassVar[str] = "letsencrypt"
    help: ClassVar[str] = "Setup Let's Encrypt SSL."
    group: ClassVar[str] = "setup"

    def run(self) -> None:
        """Issue the Let's Encrypt certificates."""
        self.bench.setup_letsencrypt(on_progress=self.report)
