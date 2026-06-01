from __future__ import annotations

import sys
from pathlib import Path

from bench_cli.config.app_config import AppConfig
from bench_cli.core.app import App
from bench_cli.core.bench import Bench
from bench_cli.managers.python_env_manager import PythonEnvManager


class GetAppCommand:
    def __init__(self, bench: Bench, repo: str, branch: str = "") -> None:
        from pathlib import PurePosixPath

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
        print(f"Installing {self.name}...")
        sys.stdout.flush()
        PythonEnvManager(self.bench).install_app(self.app)

    def _register(self) -> None:
        apps_txt = self.bench.sites_path / "apps.txt"
        existing = apps_txt.read_text().splitlines() if apps_txt.exists() else []
        if self.name not in existing:
            apps_txt.write_text("\n".join(existing + [self.name]) + "\n")

    def _build(self) -> None:
        print(f"\nSetting up assets for {self.name}...")
        sys.stdout.flush()
        PythonEnvManager(self.bench).build_assets_for_app(self.app)
