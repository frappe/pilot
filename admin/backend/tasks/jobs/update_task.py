"""
Updates all bench apps: git pull + pip install for each app.
Invoked as: python -m admin.backend.tasks.jobs.update_task <bench_root>
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from bench_cli.config.bench_config import BenchConfig
from bench_cli.core.bench import Bench
from bench_cli.managers.python_env_manager import PythonEnvManager


class UpdateJob:
    def __init__(self, bench_root: Path) -> None:
        cfg = BenchConfig.from_file(bench_root / "bench.toml")
        self.bench = Bench(cfg, bench_root)
        self.python_bin = str(bench_root / "env" / "bin" / "python")
        self.uv = PythonEnvManager(self.bench)._ensure_uv()

    def run(self) -> None:
        for app in self.bench.config.apps:
            self._update_app(app.name)
        print("\nUpdate complete.")

    def _update_app(self, app_name: str) -> None:
        app_path = self.bench.apps_path / app_name
        if not app_path.is_dir():
            print(f"Skipping {app_name}: not cloned")
            sys.stdout.flush()
            return

        print(f"\n--- Updating {app_name} ---")
        sys.stdout.flush()
        subprocess.run(["git", "pull"], cwd=str(app_path), check=False)

        print(f"Reinstalling {app_name}...")
        sys.stdout.flush()
        subprocess.run(
            [self.uv, "pip", "install", "--python", self.python_bin, "-e", str(app_path)],
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bench_root")
    args = parser.parse_args()
    UpdateJob(Path(args.bench_root)).run()


if __name__ == "__main__":
    main()
