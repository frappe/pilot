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
    help = "Uninstall an app from a site."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("site", help="Site name.")
        parser.add_argument("app", help="App name to uninstall.")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, args.site, args.app)

    def __init__(self, bench: "Bench", site_name: str, app_name: str) -> None:
        from bench_cli.config.site_config import SiteConfig
        from bench_cli.core.site import Site

        self.bench = bench
        self.site_name = site_name
        self.app_name = app_name
        self.site = Site(SiteConfig(name=site_name, apps=[]), bench)

    def run(self) -> None:
        if not self.site.exists:
            raise BenchError(f"Site '{self.site_name}' does not exist.")

        installed = self.site.list_apps()
        if installed and self.app_name not in installed:
            raise BenchError(
                f"App '{self.app_name}' is not installed on site '{self.site_name}'."
            )

        print(f"Uninstalling '{self.app_name}' from site '{self.site_name}'...")
        sys.stdout.flush()
        self.site.uninstall_app(self.app_name)
        print(f"\n'{self.app_name}' uninstalled from '{self.site_name}'.")
