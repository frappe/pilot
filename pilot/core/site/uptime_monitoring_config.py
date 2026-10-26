from __future__ import annotations

import typing
from pathlib import Path

from pilot.exceptions import BenchError
from pilot.managers.platform import is_linux

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench


class UptimeMonitorConfigurator:
    """Owns a bench's uptime log. The shared pilot-monitor service runs the pings
    (pilot.core.site.uptime_monitoring) for every bench."""

    def __init__(self, bench: "Bench | None" = None):
        self.bench = bench

    @property
    def log_path(self) -> Path:
        return self._require_bench().logs_path / "uptime.json.log"

    def setup(self) -> None:
        if not is_linux():
            raise BenchError("Uptime monitoring is only supported on linux based machines.")

        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _require_bench(self) -> "Bench":
        assert self.bench is not None, "UptimeMonitorConfigurator needs a bench for this operation"
        return self.bench
