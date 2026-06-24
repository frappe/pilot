from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from bench_cli.commands.base import Command
from bench_cli.exceptions import BenchError

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class InstallAppCommand(Command):
    name = "install-app"
    help = "Install one or more apps on a site."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("site", help="Site name.")
        parser.add_argument("apps", nargs="+", help="App name(s) to install.")
        parser.add_argument("--force", action="store_true", help="Reinstall even if already present.")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, args.site, args.apps, force=args.force)

    def __init__(self, bench: "Bench", site_name: str, app_names: list[str], *, force: bool = False) -> None:
        from bench_cli.config.site_config import SiteConfig
        from bench_cli.core.site import Site

        self.bench = bench
        self.site_name = site_name
        self.app_names = app_names
        self.force = force
        self.site = Site(SiteConfig(name=site_name, apps=[]), bench)

    def run(self) -> None:
        if not self.site.exists:
            raise BenchError(f"Site '{self.site_name}' does not exist.")

        installed = self.site.list_apps()
        for app_name in self.app_names:
            app = self.bench.app(app_name)
            if not self.force and app.config.name in installed:
                print(f"'{app_name}' is already installed on '{self.site_name}', skipping.")
                continue
            print(f"Installing '{app_name}' on '{self.site_name}'...")
            sys.stdout.flush()
            self.site.install_app(app)
            print(f"'{app_name}' installed on '{self.site_name}'.")
