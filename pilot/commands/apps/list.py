from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands.base import Command


@dataclass(kw_only=True)
class ListAppsCommand(Command):
    name: ClassVar[str] = "list-apps"
    help: ClassVar[str] = "List apps installed in the bench."

    def run(self) -> None:
        apps_txt = self.bench.sites_path / "apps.txt"
        if apps_txt.exists():
            apps = [a.strip() for a in apps_txt.read_text().splitlines() if a.strip()]
        else:
            apps = [a.config.name for a in self.bench.apps()]
        for app in apps:
            self.print(app)
