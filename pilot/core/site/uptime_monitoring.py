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

from pilot.core.notification.events import raise_event, record_alert
from pilot.core.notification.sustained import SustainedAlerts
from pilot.core.server import Server
from pilot.core.server.monitoring_datum import MetricShipper
from pilot.core.site.uptime_monitoring_config import UptimeMonitorConfigurator
from pilot.managers.gunicorn import GunicornManager
from pilot.managers.task import TaskReader

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
        """Sites still being created answer 500 until they are ready, which is not downtime."""
        provisioning = TaskReader(self.bench.path).get_provisioning_site_names()
        return [site.config.name for site in self.bench.sites() if site.config.name not in provisioning]

    def get_maintenance_sites(self) -> set[str]:
        """Sites serving frappe's 503 page on purpose, such as during an update."""
        return {site.config.name for site in self.bench.sites() if site.maintenance_mode}

    def ping_site(self, site_name: str) -> dict:
        """Ping the site through the local web server and return the result."""
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
        """The file with the uptime alert state."""
        return self._configurator.log_path.with_suffix(".alerts")

    def collect(self) -> list[dict]:
        """Ping every site on this bench once, append the results to its uptime log, and return them."""
        results = [self.ping_site(site_name) for site_name in self.get_sites()]
        maintenance_sites = self.get_maintenance_sites()
        for result in results:
            if result["site"] in maintenance_sites:
                result["maintenance"] = True
        with self._configurator.log_path.open("a") as log_file:
            for result in results:
                log_file.write(json.dumps(result) + "\n")
        self.send_alert_if_required(results)
        return results

    def send_alert_if_required(self, results: list[dict]) -> None:
        """A site that has failed its ping for five unbroken minutes is an
        incident worth waking someone for. One ping can fail for any reason.
        Sites in maintenance are down on purpose and never alert."""
        alerting = self.bench.config.resource_limits.site_uptime
        down = [result["site"] for result in results if not result["up"] and not result.get("maintenance")]
        down = down if alerting else []
        alerts = SustainedAlerts(self.alerts_path)
        due = alerts.due(down)
        up = {result["site"] for result in results if result["up"]}
        self.announce_recovered([site for site in alerts.recovered if site in up])

        recorded = [
            site
            for site in alerts.unrecorded(alerts.sustained())
            if record_alert(self.bench, self._alert_payload([site], results))
        ]
        if recorded:
            alerts.mark_recorded(recorded)

        if not due:
            return

        if self.bench.notifier.send(self._alert_payload(due, results)):
            alerts.mark_notified(due)

    def announce_recovered(self, sites: list[str]) -> None:
        """Tell webhooks that a site they heard was down answers again."""
        for site in sites:
            raise_event(
                self.bench,
                feed=False,
                event="site_up",
                title=f"{site} is reachable again",
                category="Sites",
                severity="Info",
                site=site,
                action_route=f"/sites/{site}",
            )

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
        site = down[0] if len(down) == 1 else None
        return {
            "event": SITE_DOWN_EVENT,
            "title": f"{site} is unreachable" if site else f"{len(down)} sites are unreachable",
            "category": "Sites",
            "severity": "Error",
            "site": site,
            "action_route": f"/sites/{site}" if site else "/sites",
            "message": f"{self.bench.config.name}: {', '.join(down)} unreachable",
            "context": {
                "bench": self.bench.config.name,
                "time": datetime.now(UTC).isoformat(),
                "sustained_seconds": SustainedAlerts.window_seconds,
                "sites": sites,
            },
        }

    @staticmethod
    def _result(
        site_name: str, start: float, up: bool, status_code: int | None, error: str | None = None
    ) -> dict:
        """One ping result record."""
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
    """An uptime monitor for each production bench."""
    from pilot.core.bench import Bench
    return [
        UptimeMonitor(Bench(bench_config, bench_path))
        for bench_path, bench_config in Server().benches.iter_configs()
        if bench_config.production.enabled
    ]


def main() -> None:
    """Ping the sites of every production bench, and send the count of sites in each state to Datum."""
    monitors = _production_uptime_monitors()
    results = [result for monitor in monitors for result in monitor.collect()]
    if not monitors:
        return

    shipper = MetricShipper(monitors[0].bench.config.telemetry)
    shipper.add_sites(results)
    shipper.send()


if __name__ == "__main__":
    main()
