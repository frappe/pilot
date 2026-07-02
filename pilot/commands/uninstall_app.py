from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from pilot.commands.base import Command
from pilot.commands.remove_app import RemoveAppCommand
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class UninstallAppCommand(Command):
    name = "uninstall-app"
    help = "Uninstall one or more apps from a site, also remove app from bench if not installed on any site"

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("site", help="Site name.")
        parser.add_argument("apps", nargs="+", help="App name(s) to uninstall.")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, args.site, args.apps)

    def __init__(self, bench: "Bench", site_name: str, app_names: list[str]) -> None:
        from pilot.config.site_config import SiteConfig
        from pilot.core.site import Site

        self.bench = bench
        self.site_name = site_name
        self.app_names = app_names
        self.site = Site(SiteConfig(name=site_name, apps=[]), bench)

    def remove_app_if_not_on_any_site(self, app_name: str):
        """In case the app is not installed on any site we can trigger bench remove-app"""
        for site in self.bench.sites():
            installed_apps = site.list_apps()
            if len(installed_apps) == 0 or app_name in installed_apps:
                return

        # This does run a redundant check of app installed on any site but will exit quick.
        print(f"\nApp {app_name} is not installed on any site removing from bench.")
        RemoveAppCommand(self.bench, app_name=app_name, skip_confirm=True).run()

    def run(self) -> None:
        if not self.site.exists:
            raise BenchError(f"Site '{self.site_name}' does not exist.")

        installed = self.site.list_apps()
        for app_name in self.app_names:
            app = self.bench.app(app_name)
            if installed and app.config.name not in installed:
                raise BenchError(f"App '{app_name}' is not installed on site '{self.site_name}'.")
            print(f"Uninstalling '{app_name}' from site '{self.site_name}'...")
            sys.stdout.flush()
            self.site.uninstall_app(app)
            print(f"'{app_name}' uninstalled from '{self.site_name}'.")
            self.remove_app_if_not_on_any_site(app_name)
