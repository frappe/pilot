"""Pings every production site's /api/method/ping on its local app server and
appends the result to that site's bench's uptime log. The shared pilot-monitor
service (pilot.core.server.monitoring) runs one pass every few seconds, covering
every sibling bench."""

from __future__ import annotations

import http.client
import json
import time
import typing
from datetime import UTC, datetime

from pilot.core.alerts import ALERT_SUSTAINED_SECONDS, SustainedAlerts, notify
from pilot.core.notification.events import record_alert
from pilot.core.site.uptime_monitoring_config import UptimeMonitorConfigurator
from pilot.managers.gunicorn import GunicornManager
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

    def get_maintenance_sites(self) -> set[str]:
        """Sites serving frappe's 503 page on purpose, such as during an update."""
        return {site.config.name for site in self.bench.sites() if site.maintenance_mode}

    def ping_site(self, site_name: str) -> dict:
        # Stay on loopback. Traffic over the public address keeps a Sleepy VM awake, so
        # this client follows no redirect and uses no proxy.
        connection = http.client.HTTPConnection(
            GunicornManager(self.bench).upstream_server, timeout=PING_TIMEOUT
        )
        start = time.monotonic()
        try:
            connection.request(
                "GET",
                PING_PATH,
                headers={"User-Agent": "pilot-uptime-monitor", "X-Frappe-Site-Name": site_name},
            )
            status = connection.getresponse().status
            return self._result(site_name, start, up=status == 200, status_code=status)
        except (http.client.HTTPException, OSError) as error:
            return self._result(
                site_name, start, up=False, status_code=None, error=str(error) or type(error).__name__
            )
        finally:
            connection.close()

    @property
    def alerts_path(self):
        return self._configurator.log_path.with_suffix(".alerts")

    def collect(self) -> None:
        """Ping every site on this bench once and append results to its uptime log."""
        results = [self.ping_site(site_name) for site_name in self.get_sites()]
        maintenance_sites = self.get_maintenance_sites()
        for result in results:
            if result["site"] in maintenance_sites:
                result["maintenance"] = True
        with self._configurator.log_path.open("a") as log_file:
            for result in results:
                log_file.write(json.dumps(result) + "\n")
        self.send_alert_if_required(results)

    def send_alert_if_required(self, results: list[dict]) -> None:
        """A site that has failed its ping for five unbroken minutes is an
        incident worth waking someone for. One ping can fail for any reason.
        Sites in maintenance are down on purpose and never alert."""
        alerting = self.bench.config.resource_limits.site_uptime
        down = [result["site"] for result in results if not result["up"] and not result.get("maintenance")]
        down = down if alerting else []
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
                "error": last_seen[site].get("error"),
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
    def _result(
        site_name: str, start: float, up: bool, status_code: int | None, error: str | None = None
    ) -> dict:
        result = {
            "time": datetime.now(UTC).isoformat(),
            "site": site_name,
            "up": up,
            "status_code": status_code,
            "response_ms": int((time.monotonic() - start) * 1000),
        }
        # A failure with no HTTP status (refused connection, timeout) keeps its reason.
        if error:
            result["error"] = error
        return result


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
