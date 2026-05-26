"""
Switches an app to a different git branch, reinstalls it, and rebuilds assets.
Invoked as: python -m admin.backend.tasks.jobs.switch_branch_task <bench_root> <app_name> <branch>
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path

from bench_cli.config.bench_config import BenchConfig
from bench_cli.core.bench import Bench
from bench_cli.managers.python_env_manager import PythonEnvManager
from bench_cli.utils import write_toml

from admin.backend.tasks.jobs.build_assets import build_app_assets


class SwitchBranchJob:
    def __init__(self, bench_root: Path, app_name: str, branch: str) -> None:
        cfg = BenchConfig.from_file(bench_root / "bench.toml")
        self.bench = Bench(cfg, bench_root)
        self.app_name = app_name
        self.branch = branch
        self.app_path = bench_root / "apps" / app_name
        self.python_bin = str(bench_root / "env" / "bin" / "python")
        self.uv = PythonEnvManager(self.bench)._ensure_uv()

    def run(self) -> None:
        if not (self.app_path / ".git").exists():
            print(f"Error: '{self.app_name}' is not cloned at {self.app_path}")
            sys.exit(1)

        stashed = self._prepare_repo()
        self._checkout(stashed)
        self._reinstall()
        self._update_bench_toml()
        build_app_assets(self.bench.path, self.app_name)
        print(f"\n'{self.app_name}' switched to '{self.branch}' successfully.")

    def _prepare_repo(self) -> bool:
        print(f"Fetching all remote branches for {self.app_name}...")
        sys.stdout.flush()
        subprocess.run(
            ["git", "-C", str(self.app_path), "fetch", "origin", "+refs/heads/*:refs/remotes/origin/*"],
            check=False,
        )
        subprocess.run(["git", "-C", str(self.app_path), "merge", "--abort"], capture_output=True, check=False)
        subprocess.run(["git", "-C", str(self.app_path), "rebase", "--abort"], capture_output=True, check=False)
        result = subprocess.run(
            ["git", "-C", str(self.app_path), "stash", "--include-untracked"],
            capture_output=True, text=True, check=False,
        )
        return "No local changes" not in result.stdout

    def _checkout(self, stashed: bool) -> None:
        print(f"Switching to branch '{self.branch}'...")
        sys.stdout.flush()
        result = subprocess.run(
            ["git", "-C", str(self.app_path), "checkout", "-B", self.branch, f"origin/{self.branch}"],
            check=False,
        )
        if result.returncode != 0:
            if stashed:
                subprocess.run(["git", "-C", str(self.app_path), "stash", "pop"], check=False)
            print(f"Error: could not switch to branch '{self.branch}'")
            sys.exit(result.returncode)

    def _reinstall(self) -> None:
        print(f"Reinstalling {self.app_name}...")
        sys.stdout.flush()
        subprocess.run(
            [self.uv, "pip", "install", "--python", self.python_bin, "-e", str(self.app_path)],
            check=False,
        )

    def _update_bench_toml(self) -> None:
        bench_toml = self.bench.path / "bench.toml"
        with bench_toml.open("rb") as fh:
            raw = tomllib.load(fh)
        for app_entry in raw.get("apps", []):
            if app_entry.get("name") == self.app_name:
                app_entry["branch"] = self.branch
                break
        write_toml(bench_toml, raw)
        print(f"Updated bench.toml: {self.app_name} -> {self.branch}")
        sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bench_root")
    parser.add_argument("app_name")
    parser.add_argument("branch")
    args = parser.parse_args()
    SwitchBranchJob(Path(args.bench_root), args.app_name, args.branch).run()


if __name__ == "__main__":
    main()
