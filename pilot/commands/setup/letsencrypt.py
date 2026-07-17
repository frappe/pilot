from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands.base import Command


@dataclass(kw_only=True)
class SetupLetsEncryptCommand(Command):
    name: ClassVar[str] = "letsencrypt"
    help: ClassVar[str] = "Setup Let's Encrypt SSL."
    group: ClassVar[str] = "setup"

    def run(self) -> None:
        self.bench.setup_letsencrypt()
