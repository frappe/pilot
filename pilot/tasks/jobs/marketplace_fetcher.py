from __future__ import annotations

from typing import Callable

from pilot.commands.get_app import GetAppCommand
from pilot.core.marketplace import Marketplace
from pilot.exceptions import BenchError


class MarketplaceFetcher:
    """Fetches a marketplace app and its dependencies via `bench get-app`,
    reporting each fetch through a task's `_step` callback."""

    def __init__(self, bench, step: Callable[[str, str], None]) -> None:
        self._bench = bench
        self._step = step

    def fetch(self, app_name: str) -> list[GetAppCommand]:
        apps = Marketplace(self._bench).read_all_apps()
        resolver = next((a for a in apps if a.app == app_name), None)
        if not resolver:
            raise BenchError(f"'{app_name}' not found in marketplace.")
        cmds = []
        for dep in resolver.resolve():
            self._step("fetch", f"Fetch {dep.app}")
            cmd = GetAppCommand(self._bench, dep.repo, dep.target)
            cmd.run()
            cmds.append(cmd)
        return cmds
