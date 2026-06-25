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
        self._normalize_folder()
        self._install()
        self._validate()
        self._register()
        self._build()
        print(f"\n'{self.name}' installed successfully.")

    def _clone(self) -> None:
        # is_cloned resolves the normalized (module-name) folder too, so a re-run
        # finds an existing clone however its folder was named.
        if self.app.is_cloned:
            print(f"'{self.name}' already cloned, skipping clone.")
            sys.stdout.flush()
            return
        print(f"Cloning {self.name}...")
        sys.stdout.flush()
        self.app.clone()

    def _normalize_folder(self) -> None:
        """Frappe identifies an app by its directory name and assumes that name
        is the importable module (it builds assets, runs after_build hooks and
        imports the package all by that one name). Rename the clone to the module
        name (e.g. india-compliance -> india_compliance) so every frappe code
        path agrees — matching what legacy bench does at clone time."""
        module = self.app.module_name
        if module == self.app.config.name:
            return
        target = self.bench.apps_path / module
        # Rename a fresh clone into place; a prior run may have already normalized
        # it, in which case we just adopt the existing module-name folder.
        if not target.exists():
            self.app.path.rename(target)
        self._set_app(module)

    def _set_app(self, name: str) -> None:
        from bench_cli.config.app_config import AppConfig
        from bench_cli.core.app import App

        self.name = name
        self.app = App(AppConfig(name=name, repo=self.repo, branch=self.app.config.branch), self.bench)

    def _install(self) -> None:
        from bench_cli.managers.python_env_manager import PythonEnvManager

        print(f"Installing {self.name}...")
        sys.stdout.flush()
        PythonEnvManager(self.bench).install_app(self.app)

    def _register(self) -> None:
        # apps.txt lists the importable package name; the folder was normalized to
        # that name in _normalize_folder, so self.name is it.
        apps_txt = self.bench.sites_path / "apps.txt"
        existing = apps_txt.read_text().splitlines() if apps_txt.exists() else []
        if self.name not in existing:
            apps_txt.write_text("\n".join(existing + [self.name]) + "\n")

    def _validate(self) -> None:
        import subprocess

        from bench_cli.exceptions import BenchError

        python = str(self.bench.env_path / "bin" / "python")
        result = subprocess.run(
            [python, "-c", f"import {self.name}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # Roll back: remove from apps dir so a broken app doesn't crash workers.
            import shutil

            shutil.rmtree(self.app.path, ignore_errors=True)
            raise BenchError(
                f"App '{self.name}' installed but its Python package "
                f"could not be imported.\n"
                f"  This usually means the app's package name does not match\n"
                f"  its declared name (check pyproject.toml / hooks.py app_name).\n"
                f"  Error: {result.stderr.strip()}"
            )

    def _build(self) -> None:
        from bench_cli.managers.python_env_manager import PythonEnvManager

        print(f"\nSetting up assets for {self.name}...")
        sys.stdout.flush()
        PythonEnvManager(self.bench).build_assets_for_app(self.app)
