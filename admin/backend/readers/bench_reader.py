from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pilot.config.bench_config import BenchConfig


@dataclass
class BenchSummary:
    name: str
    python_version: str
    app_count: int
    site_count: int


class BenchReader:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def config(self) -> BenchConfig:
        return BenchConfig.from_file(self._bench_root / "bench.toml")

    def summary(self) -> BenchSummary:
        config = self.config()
        apps_path = self._bench_root / "apps"
        sites_path = self._bench_root / "sites"
        app_count = sum(
            1 for d in apps_path.iterdir() if d.is_dir() and (d / ".git").exists()
        ) if apps_path.is_dir() else 0
        site_count = sum(
            1 for d in sites_path.iterdir() if d.is_dir() and (d / "site_config.json").exists()
        ) if sites_path.is_dir() else 0
        return BenchSummary(
            name=config.name,
            python_version=config.python_version,
            app_count=app_count,
            site_count=site_count,
        )
