from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from pilot.commands.base import Command

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.bench import Bench


class GetAppCommand(Command):
    name = "get-app"
    help = "Clone and install an app."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("repo", help="Git repository URL.")
        parser.add_argument("--branch", default="", help="Git branch to checkout.")
        parser.add_argument(
            "--install-dependencies",
            action="store_true",
            default=False,
            help="Install app dependencies",
        )
        parser.add_argument(
            "--skip-validations",
            action="store_true",
            default=False,
            help="Skip running app validations",
        )

    @classmethod
    def from_args(cls, args, bench):
        return cls(
            bench,
            args.repo,
            args.branch or "main",
            install_dependencies=args.install_dependencies,
            skip_validations=args.skip_validations,
        )

    def __init__(
        self,
        bench: "Bench",
        repo: str,
        branch: str = "",
        install_dependencies: bool = False,
        skip_validations: bool = False,
    ) -> None:
        from pilot.core.app import App

        self.bench = bench
        self.install_dependencies = install_dependencies
        self.skip_validations = skip_validations
        self.app = App.from_repo(bench, repo, branch)
        self.installed_dependencies: list[App] = []

    def run(self) -> None:
        result = self.app.install(
            install_dependencies=self.install_dependencies,
            skip_validations=self.skip_validations,
            on_progress=self.report,
        )
        self.app = result.app
        self.installed_dependencies = result.installed_dependencies
