"""Pings every production site's /api/method/ping and appends the result to
that site's bench's uptime log. Invoked by the shared pilot-uptime systemd
timer (pilot.core.site.uptime_monitoring_config); one pass per invocation,
covering every sibling bench - the timer itself controls the interval."""

from __future__ import annotations

import json
import time
import typing
import urllib.error
import urllib.request
from datetime import UTC, datetime

from pilot.core.alerts import ALERT_SUSTAINED_SECONDS, SustainedAlerts, notify
from pilot.core.notification.events import record_alert
from pilot.core.site.uptime_monitoring_config import UptimeMonitorConfigurator
from pilot.utils import cli_root, iter_sibling_benches

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

PING_TIMEOUT = 5.0
PING_PATH = "/api/method/ping"
SITE_DOWN_EVENT = "site_down"


class UptimeMonitor:
    def __init__(self, bench: "Bench"):
        self.bench = bench
        self._configurator = UptimeMonitorConfigurator(bench)

    def get_sites(self) -> list[str]:
        return [site.config.name for site in self.bench.sites()]

    def ping_site(self, site_name: str) -> dict:
        url = f"https://{site_name}{PING_PATH}"
        start = time.monotonic()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "pilot-uptime-monitor"})
            with urllib.request.urlopen(request, timeout=PING_TIMEOUT) as response:
                return self._result(site_name, start, up=response.status == 200, status_code=response.status)
        except urllib.error.HTTPError as error:
            return self._result(site_name, start, up=False, status_code=error.code)
        except (urllib.error.URLError, TimeoutError, OSError):
            return self._result(site_name, start, up=False, status_code=None)

    @property
    def alerts_path(self):
        return self._configurator.log_path.with_suffix(".alerts")

    def collect(self) -> None:
        """Ping every site on this bench once and append results to its uptime log."""
        results = [self.ping_site(site_name) for site_name in self.get_sites()]
        with self._configurator.log_path.open("a") as log_file:
            for result in results:
                log_file.write(json.dumps(result) + "\n")
        self.send_alert_if_required(results)

    def send_alert_if_required(self, results: list[dict]) -> None:
        """A site that has failed its ping for five unbroken minutes is an
        incident worth waking someone for. One ping can fail for any reason."""
        alerting = self.bench.config.resource_limits.site_uptime
        down = [result["site"] for result in results if not result["up"]] if alerting else []
        alerts = SustainedAlerts(self.alerts_path)
        due = alerts.due(down)

        recorded = [
            site
            for site in alerts.unrecorded(alerts.sustained())
            if record_alert(
                self.bench,
                self._alert_payload([site], results),
                category="Sites",
                severity="Error",
                title=f"{site} is unreachable",
                site=site,
            )
        ]
        if recorded:
            alerts.mark_recorded(recorded)

        if not due:
            return

        if notify(self.bench, self._alert_payload(due, results)):
            alerts.mark_notified(due)

    def _alert_payload(self, down: list[str], results: list[dict]) -> dict:
        """Same event/message/context shape the resource alerts use."""
        last_seen = {result["site"]: result for result in results}
        sites = [
            {
                "site": site,
                "status_code": last_seen[site]["status_code"],
                "response_ms": last_seen[site]["response_ms"],
            }
            for site in down
        ]
        return {
            "event": SITE_DOWN_EVENT,
            "message": f"{self.bench.config.name}: {', '.join(down)} unreachable",
            "context": {
                "bench": self.bench.config.name,
                "time": datetime.now(UTC).isoformat(),
                "sustained_seconds": ALERT_SUSTAINED_SECONDS,
                "sites": sites,
            },
        }

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
