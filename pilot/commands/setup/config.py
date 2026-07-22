from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import Command


@dataclass(kw_only=True)
class UpdateConfigCommand(Command):
    name: ClassVar[str] = "config"
    help: ClassVar[str] = "Regenerate config files from bench.toml."
    group: ClassVar[str] = "setup"

    def run(self) -> None:
        self.report("Updating Redis configs...")
        self.report("Updating process manager config...")
        self.report("Updating common_site_config.json...")
        self.bench.rebuild_runtime_config()
        if self.bench.config.production.enabled:
            self.report("  Note: run 'bench setup nginx' to reload nginx with the new config.")
