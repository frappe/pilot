"""
Clones an app repo and pip-installs it into the bench virtualenv.
Invoked as: python -m admin.backend.tasks.jobs.get_app_task <bench_root> <name> <repo> [--branch <branch>]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bench_cli.config.app_config import AppConfig
from bench_cli.config.bench_config import BenchConfig
from bench_cli.core.app import App
from bench_cli.core.bench import Bench
from bench_cli.managers.python_env_manager import PythonEnvManager

from admin.backend.tasks.jobs.build_assets import build_app_assets


class GetAppJob:
    def __init__(self, bench_root: Path, name: str, repo: str, branch: str) -> None:
        cfg = BenchConfig.from_file(bench_root / "bench.toml")
        self.bench = Bench(cfg, bench_root)
        self.app = App(AppConfig(name=name, repo=repo, branch=branch or "main"), self.bench)
        self.name = name
        self.repo = repo

    def run(self) -> None:
        self._clone()
        self._install()
        self._register()
        self._build()
        print(f"\n'{self.name}' installed successfully.")

    def _clone(self) -> None:
        if self.app.is_cloned:
            print(f"'{self.name}' is already cloned at {self.app.path}. Skipping clone.")
        else:
            print(f"Cloning {self.name} from {self.repo}...")
            self.app.clone()
        sys.stdout.flush()

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
        build_app_assets(self.bench.path, self.name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bench_root")
    parser.add_argument("name")
    parser.add_argument("repo")
    parser.add_argument("--branch", default="")
    args = parser.parse_args()
    GetAppJob(Path(args.bench_root), args.name, args.repo, args.branch).run()


if __name__ == "__main__":
    main()
