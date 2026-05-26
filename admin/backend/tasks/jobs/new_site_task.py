"""
Creates a new Frappe site.
Invoked as: python -m admin.backend.tasks.jobs.new_site_task <bench_root> <site_name>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bench_cli.config.bench_config import BenchConfig
from bench_cli.config.site_config import SiteConfig
from bench_cli.core.bench import Bench
from bench_cli.core.site import Site


class NewSiteJob:
    def __init__(self, bench_root: Path, site_name: str, admin_password: str) -> None:
        cfg = BenchConfig.from_file(bench_root / "bench.toml")
        self.bench = Bench(cfg, bench_root)
        self.site = Site(SiteConfig(name=site_name, apps=[], admin_password=admin_password), self.bench)
        self.site_name = site_name

    def run(self) -> None:
        if self.site.exists:
            print(f"Site '{self.site_name}' already exists. Skipping creation.")
            sys.stdout.flush()
            return

        self._create()
        self._refresh_common_config()
        print(f"\nSite '{self.site_name}' created successfully.")

    def _create(self) -> None:
        print(f"Creating site '{self.site_name}'...")
        sys.stdout.flush()
        self.site.create()

    def _refresh_common_config(self) -> None:
        self.bench.write_common_site_config()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bench_root")
    parser.add_argument("site_name")
    parser.add_argument("--admin-password", default="admin")
    args = parser.parse_args()
    NewSiteJob(Path(args.bench_root), args.site_name, args.admin_password).run()


if __name__ == "__main__":
    main()
