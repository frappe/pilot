"""Pings every production site's /api/method/ping and appends the result to
that site's bench's uptime log. Invoked by the shared site-uptime systemd
timer (pilot.core.site.uptime_monitoring_config); one pass per invocation,
covering every sibling bench - the timer itself controls the interval."""

from __future__ import annotations

import json
import time
import typing
import urllib.error
import urllib.request
from datetime import UTC, datetime

from pilot.core.site.uptime_monitoring_config import UptimeMonitorConfigurator
from pilot.utils import cli_root, iter_sibling_benches

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

PING_TIMEOUT = 5.0


class UptimeMonitor:
    def __init__(self, bench: "Bench"):
        self.bench = bench
        self._configurator = UptimeMonitorConfigurator(bench)
        self._configurator.setup()

    def get_sites(self) -> list[str]:
        return [site.config.name for site in self.bench.sites()]

    def ping_site(self, site_name: str) -> dict:
        url = f"https://{site_name}/api/method/ping"
        start = time.monotonic()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "pilot-uptime-monitor"})
            with urllib.request.urlopen(request, timeout=PING_TIMEOUT) as response:
                return self._result(site_name, start, up=response.status == 200, status_code=response.status)
        except urllib.error.HTTPError as error:
            return self._result(site_name, start, up=False, status_code=error.code)
        except (urllib.error.URLError, TimeoutError, OSError):
            return self._result(site_name, start, up=False, status_code=None)

    def collect(self) -> None:
        """Ping every site on this bench once and append results to its uptime log."""
        with self._configurator.log_path.open("a") as log_file:
            for site_name in self.get_sites():
                log_file.write(json.dumps(self.ping_site(site_name)) + "\n")

    @staticmethod
    def _result(site_name: str, start: float, up: bool, status_code: int | None) -> dict:
        return {
            "time": datetime.now(UTC).isoformat(),
            "site": site_name,
            "up": up,
            "status_code": status_code,
            "response_ms": int((time.monotonic() - start) * 1000),
        }


def _production_uptime_monitors() -> list[UptimeMonitor]:
    from pilot.core.bench import Bench

    sentinel = cli_root() / "benches" / ".uptime-placeholder"
    return [
        UptimeMonitor(Bench(bench_config, bench_path))
        for bench_path, bench_config in iter_sibling_benches(sentinel)
        if bench_config.production.enabled
    ]


def main() -> None:
    for monitor in _production_uptime_monitors():
        monitor.collect()


if __name__ == "__main__":
    main()
