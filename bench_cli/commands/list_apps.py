from __future__ import annotations

from typing import TYPE_CHECKING

from bench_cli.commands.base import Command

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class ListAppsCommand(Command):
    name = "list-apps"
    help = "List apps installed in the bench."

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def run(self) -> None:
        apps_txt = self.bench.sites_path / "apps.txt"
        if apps_txt.exists():
            apps = [a.strip() for a in apps_txt.read_text().splitlines() if a.strip()]
        else:
            apps = [a.config.name for a in self.bench.apps()]
        for app in apps:
            print(app)
