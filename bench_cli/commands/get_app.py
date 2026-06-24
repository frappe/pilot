from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from bench_cli.commands.base import Command

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class GetAppCommand(Command):
    name = "get-app"
    help = "Clone and install an app."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("repo", help="Git repository URL.")
        parser.add_argument("--branch", default="", help="Git branch to checkout.")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, args.repo, args.branch or "main")

    def __init__(self, bench: "Bench", repo: str, branch: str = "") -> None:
        from pathlib import PurePosixPath

        from bench_cli.config.app_config import AppConfig
        from bench_cli.core.app import App

        name = PurePosixPath(repo.rstrip("/")).name
        if name.endswith(".git"):
            name = name[:-4]

        self.bench = bench
        self.repo = repo
        self.name = name
        self.app = App(AppConfig(name=name, repo=repo, branch=branch), bench)

    def run(self) -> None:
        self._clone()
        self._install()
        self._validate()
        self._register()
        self._build()
        print(f"\n'{self.name}' installed successfully.")

    def _clone(self) -> None:
        if self.app.is_cloned:
            print(f"'{self.name}' already cloned at {self.app.path}, skipping clone.")
        else:
            print(f"Cloning {self.name}...")
        sys.stdout.flush()
        if not self.app.is_cloned:
            self.app.clone()

    def _install(self) -> None:
        from bench_cli.managers.python_env_manager import PythonEnvManager

        print(f"Installing {self.name}...")
        sys.stdout.flush()
        PythonEnvManager(self.bench).install_app(self.app)

    def _register(self) -> None:
        # apps.txt must list the importable package name (Frappe imports each
        # entry by name), which can differ from the repo/folder name.
        module = self.app.module_name
        apps_txt = self.bench.sites_path / "apps.txt"
        existing = apps_txt.read_text().splitlines() if apps_txt.exists() else []
        if module not in existing:
            apps_txt.write_text("\n".join(existing + [module]) + "\n")

    def _validate(self) -> None:
        import subprocess

        from bench_cli.exceptions import BenchError

        module = self.app.module_name
        python = str(self.bench.env_path / "bin" / "python")
        result = subprocess.run(
            [python, "-c", f"import {module}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # Roll back: remove from apps dir so a broken app doesn't crash workers.
            import shutil

            shutil.rmtree(self.app.path, ignore_errors=True)
            raise BenchError(
                f"App '{self.name}' installed but its Python package "
                f"('{module}') could not be imported.\n"
                f"  This usually means the app's package name does not match\n"
                f"  its declared name (check pyproject.toml / hooks.py app_name).\n"
                f"  Error: {result.stderr.strip()}"
            )

    def _build(self) -> None:
        from bench_cli.managers.python_env_manager import PythonEnvManager

        print(f"\nSetting up assets for {self.name}...")
        sys.stdout.flush()
        PythonEnvManager(self.bench).build_assets_for_app(self.app)
