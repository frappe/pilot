"""
Drops a Frappe site and removes it from bench.toml.
Invoked as: python -m admin.backend.tasks.jobs.drop_site_task <bench_root> <site_name>
"""
from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from bench_cli.config.bench_config import BenchConfig
from bench_cli.core.bench import Bench
from bench_cli.utils import run_command, write_toml


class DropSiteJob:
    def __init__(self, bench_root: Path, site_name: str) -> None:
        cfg = BenchConfig.from_file(bench_root / "bench.toml")
        self.bench = Bench(cfg, bench_root)
        self.site_name = site_name
        self.mariadb = cfg.mariadb

    def run(self) -> None:
        self._drop()
        self._remove_from_bench_toml()
        print(f"\nSite '{self.site_name}' dropped and removed from bench.toml.")

    def _drop(self) -> None:
        print(f"Dropping site '{self.site_name}'...")
        sys.stdout.flush()
        bench_bin = str(self.bench.env_path / "bin" / "bench")
        cmd = [bench_bin, "frappe", "drop-site", "--force", self.site_name]
        if self.mariadb.root_password:
            cmd += ["--db-root-password", self.mariadb.root_password]
        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)

    def _remove_from_bench_toml(self) -> None:
        bench_toml = self.bench.path / "bench.toml"
        with bench_toml.open("rb") as fh:
            raw = tomllib.load(fh)
        raw["sites"] = [s for s in raw.get("sites", []) if s.get("name") != self.site_name]
        write_toml(bench_toml, raw)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bench_root")
    parser.add_argument("site_name")
    args = parser.parse_args()
    DropSiteJob(Path(args.bench_root), args.site_name).run()


if __name__ == "__main__":
    main()
