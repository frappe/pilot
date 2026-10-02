from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from pilot.core.bench.fork_runtime import ForkRuntime
from pilot.core.site.template import SiteTemplate
from pilot.exceptions import BenchError
from pilot.internal.atomic_file import exclusive_file_lock

if TYPE_CHECKING:
    from pathlib import Path

    from pilot.core.bench import Bench


class FreshForkTemplate:
    """Snapshot a source site for one fresh fork, copying existing built assets directly."""

    def __init__(self, bench: Bench) -> None:
        self.bench = bench

    def prepare(self, name: str = "", on_progress=print) -> Path:
        if self.bench.config.production.enabled:
            raise BenchError("Fork a development bench, not a production bench.")
        if name:
            site = self.bench.site(name)
        else:
            sites = self.bench.sites()
            if len(sites) != 1:
                raise BenchError("Select the source site with --site when the bench has multiple sites.")
            site = sites[0]
        if not (site.path / "site_config.json").is_file():
            raise BenchError(f"Source site does not exist: {site.config.name}")
        path = self.bench.path.parent / ".fork-templates" / secrets.token_hex(16)
        with (
            exclusive_file_lock(self.bench.path / "fork-snapshot"),
            ForkRuntime(self.bench, allow_existing=True),
        ):
            SiteTemplate(path).prepare(site, on_progress, build_assets=False, reuse_source_artifacts=True)
        return path
