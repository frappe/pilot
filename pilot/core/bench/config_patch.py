from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pilot.config import BenchConfig
from pilot.config.common import CommonConfig
from pilot.config.common_site_config import update_common_site_config
from pilot.exceptions import ConfigError
from pilot.internal.json_merge_patch import apply_merge_patch

if TYPE_CHECKING:
    from pilot.core.bench import Bench


@dataclass
class ConfigPatch:
    """JSON Merge Patches for the config files of a bench."""

    common_config: dict[str, Any] = field(default_factory=dict)
    bench_config: dict[str, Any] = field(default_factory=dict)
    common_site_config: dict[str, Any] = field(default_factory=dict)

    def apply(self, bench: Bench) -> None:
        if unknown := BenchConfig.unknown_config_paths(self.bench_config):
            raise ConfigError(f"bench.toml patch has unrecognized fields: {', '.join(unknown)}")

        if self.common_config:
            with CommonConfig.open(bench.path.parent, mode="raw") as data:
                apply_merge_patch(data, self.common_config)
                BenchConfig.read(bench.path, common=CommonConfig.from_raw_dict(data, strict=True))

        if self.bench_config:
            with BenchConfig.open(bench.path, mode="raw") as data:
                apply_merge_patch(data, self.bench_config)

        if self.common_site_config:
            with update_common_site_config(bench.sites_path) as data:
                apply_merge_patch(data, self.common_site_config)
