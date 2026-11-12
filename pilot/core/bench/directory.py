from __future__ import annotations

import json
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.internal.hostnames import normalize_host

if TYPE_CHECKING:
    from pilot.config import BenchConfig


class BenchDirectory:
    """The benches that share one parent directory, as a host sees them.

    Used to find ports and hostnames that another bench already holds. `exclude` leaves out
    the asking bench, which checks itself separately."""

    def __init__(self, path: Path, exclude: Path | None = None) -> None:
        self.path = path
        self.exclude = exclude

    @classmethod
    def siblings_of(cls, bench_path: Path) -> BenchDirectory:
        """Every other bench next to this one."""
        return cls(bench_path.parent, exclude=bench_path)

    def iter_configs(self) -> Iterator[tuple[Path, BenchConfig]]:
        """Each bench's path and config, merged with the shared common_config.toml."""
        from pilot.config import BenchConfig
        from pilot.config.common import CommonConfig

        if not self.path.is_dir():
            return
        common = CommonConfig.read(self.path)
        excluded = self.exclude.resolve() if self.exclude else None
        for bench_path in self.path.iterdir():
            toml_path = bench_path / "bench.toml"
            if not bench_path.is_dir() or bench_path.resolve() == excluded or not toml_path.exists():
                continue
            try:
                # Half-configured benches still count for ports and hostnames.
                yield bench_path, BenchConfig._from_dict(tomllib.loads(toml_path.read_text()), common=common)
            except Exception:
                continue

    def get_host_owner(self, host: str) -> str | None:
        """The name of the bench that already claims this hostname, if any."""
        from pilot.core.bench import Bench

        target = normalize_host(host)
        if not target:
            return None
        for bench_path, config in self.iter_configs():
            if target in self.get_claimed_hosts(bench_path, config):
                return config.name
            if Bench(config, bench_path).site_aliases.pending_owner(target):
                return config.name
        return None

    @classmethod
    def get_claimed_hosts(cls, bench_path: Path, config: BenchConfig) -> set[str]:
        """Every hostname a bench answers to: its admin domain, sites, domains and pinned certificates."""
        hosts = {normalize_host(config.admin.domain)} if config.admin.domain else set()
        sites_path = bench_path / "sites"
        if not sites_path.is_dir():
            return hosts
        for site_path in sites_path.iterdir():
            config_path = site_path / "site_config.json"
            if config_path.exists():
                hosts.add(normalize_host(site_path.name))
                hosts.update(cls.get_site_config_hosts(config_path))
        return hosts

    @staticmethod
    def get_site_config_hosts(config_path: Path) -> set[str]:
        """Custom domains and the pinned certificate name from one site_config.json."""
        try:
            config = json.loads(config_path.read_text())
        except Exception:
            return set()
        if not isinstance(config, dict):
            return set()
        hosts = set()
        for alias in config.get("domains", []) or []:
            name = alias.get("domain") if isinstance(alias, dict) else alias
            if name:
                hosts.add(normalize_host(str(name)))
        if pinned := config.get("cert_name"):
            hosts.add(normalize_host(str(pinned)))
        return hosts
