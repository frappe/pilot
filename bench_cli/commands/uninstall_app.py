from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from bench_cli.commands.base import Command
from bench_cli.exceptions import BenchError

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class UninstallAppCommand(Command):
    name = "uninstall-app"
    help = "Uninstall one or more apps from a site."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("site", help="Site name.")
        parser.add_argument("apps", nargs="+", help="App name(s) to uninstall.")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, args.site, args.apps)

    def __init__(self, bench: "Bench", site_name: str, app_names: list[str]) -> None:
        from bench_cli.config.site_config import SiteConfig
        from bench_cli.core.site import Site

        self.bench = bench
        self.site_name = site_name
        self.app_names = app_names
        self.site = Site(SiteConfig(name=site_name, apps=[]), bench)

    def run(self) -> None:
        if not self.site.exists:
            raise BenchError(f"Site '{self.site_name}' does not exist.")

        installed = self.site.list_apps()
        for app_name in self.app_names:
            app = self.bench.app(app_name)
            if installed and app.config.name not in installed:
                raise BenchError(
                    f"App '{app_name}' is not installed on site '{self.site_name}'."
                )
            print(f"Uninstalling '{app_name}' from site '{self.site_name}'...")
            sys.stdout.flush()
            self.site.uninstall_app(app)
            print(f"'{app_name}' uninstalled from '{self.site_name}'.")
