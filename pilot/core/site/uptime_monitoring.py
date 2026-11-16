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

from pilot.core.notification.alert_log import FIRING, RESOLVED, AlertLog
from pilot.core.server import Server
from pilot.core.server.monitoring_datum import TelemetryShipper
from pilot.core.site.uptime_monitoring_config import UptimeMonitorConfigurator
from pilot.managers.gunicorn import GunicornManager
from pilot.managers.task import TaskReader

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

PING_TIMEOUT = 5.0
PING_PATH = "/api/method/ping"
# Enough for the JSON error body of Frappe, which names the exception.
MAX_ERROR_BODY = 65536
# Exceptions that come from the database, Redis or the network, not from app code.
PLATFORM_EXCEPTIONS = frozenset(
    {"OperationalError", "InterfaceError", "InternalError", "ConnectionError", "TimeoutError"}
)
SITE_DOWN_EVENT = "site_down"
SITE_UP_EVENT = "site_up"


class UptimeMonitor:
    # How long a site must fail, or pass, its ping before its alert fires, or resolves.
    alert_window_seconds = 60

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
            response = connection.getresponse()
            if response.status == 200:
                return self._result(site_name, start, up=True, status_code=200)
            fault = get_ping_fault(response.status, response.read(MAX_ERROR_BODY))
            return self._result(site_name, start, up=False, status_code=response.status, fault=fault)
        except (http.client.HTTPException, OSError) as error:
            return self._result(
                site_name,
                start,
                up=False,
                status_code=None,
                fault="platform",
                error=str(error) or type(error).__name__,
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
        """A site that fails its ping for the whole window fires an alert, which resolves once the site
        answers for the whole window. One ping can fail for any reason. A site in maintenance keeps its
        state. A site that is gone, or alerting that is off, drops its alert without a resolution."""
        tracked = {result["site"]: result for result in results}
        if not self.bench.config.resource_limits.site_uptime:
            tracked = {}
        alerts = AlertLog(self.alerts_path, self.alert_window_seconds)
        alerts.retain(tracked)
        down = {
            site: result for site, result in tracked.items() if not result["up"] and not result.get("maintenance")
        }
        alerts.update(active=down, clear=[site for site, result in tracked.items() if result["up"]])
        alerts.deliver(self.bench, FIRING, lambda sites: self.get_down_payload(alerts.get_details(sites)[0]))
        alerts.deliver(self.bench, RESOLVED, lambda sites: self.get_recovered_payload(sites[0]))

    def get_down_payload(self, result: dict) -> dict:
        """The site_down alert for the failed ping it fired on. Central names the site from the Site reference."""
        site = result["site"]
        return {
            "event": SITE_DOWN_EVENT,
            "title": f"{site} is unreachable",
            "category": "Sites",
            "severity": "Error",
            "site": site,
            "action_route": f"/sites/{site}",
            "message": f"Health check failed for {self.alert_window_seconds} seconds ({get_failure_reason(result)}).",
            "reference_doctype": "Site",
            "reference_name": site,
            "context": {
                "bench": self.bench.config.name,
                "time": datetime.now(UTC).isoformat(),
                "sustained_seconds": self.alert_window_seconds,
                "sites": [
                    {
                        "site": site,
                        "status_code": result["status_code"],
                        "response_ms": result["response_ms"],
                        "error": result.get("error"),
                    }
                ],
            },
        }

    def get_recovered_payload(self, site: str) -> dict:
        """The site_up event that resolves a site_down alert."""
        return {
            "event": SITE_UP_EVENT,
            "title": f"{site} is reachable again",
            "category": "Sites",
            "severity": "Info",
            "site": site,
            "action_route": f"/sites/{site}",
            "message": f"Health check passed for {self.alert_window_seconds} seconds.",
            "reference_doctype": "Site",
            "reference_name": site,
            "context": {"bench": self.bench.config.name, "time": datetime.now(UTC).isoformat(), "site": site},
        }

    @staticmethod
    def _result(
        site_name: str,
        start: float,
        up: bool,
        status_code: int | None,
        fault: str | None = None,
        error: str | None = None,
    ) -> dict:
        """One ping result record. A failed ping has the fault: `app` or `platform`."""
        result = {
            "time": datetime.now(UTC).isoformat(),
            "site": site_name,
            "up": up,
            "status_code": status_code,
            "response_ms": int((time.monotonic() - start) * 1000),
        }
        if fault:
            result["fault"] = fault
        # A failure with no HTTP status (refused connection, timeout) keeps its reason.
        if error:
            result["error"] = error
        return result


def get_failure_reason(result: dict) -> str:
    """Why a ping failed, in a few words: the HTTP status, or the error when there was no answer."""
    if result["status_code"]:
        return f"HTTP {result['status_code']}"
    return result.get("error") or "no answer"


def get_ping_fault(status: int, body: bytes) -> str:
    """Whether a failed ping is the fault of app code or of the platform.

    Request hooks of installed apps run on the ping. A 4xx other than 404 comes from such a hook. A 500
    whose JSON body names an exception that is not in PLATFORM_EXCEPTIONS comes from app code. Every other
    answer is the platform's fault: 404 (the site is missing), a redirect, a 5xx without an exception name,
    or a database, Redis or network error."""
    if 400 <= status < 500 and status != 404:
        return "app"
    if status != 500:
        return "platform"
    try:
        exception = json.loads(body).get("exc_type")
    except (ValueError, AttributeError):
        return "platform"
    is_app_exception = isinstance(exception, str) and exception not in PLATFORM_EXCEPTIONS
    return "app" if is_app_exception else "platform"


def _production_uptime_monitors() -> list[UptimeMonitor]:
    """An uptime monitor for each production bench."""
    from pilot.core.bench import Bench
    return [
        UptimeMonitor(Bench(bench_config, bench_path))
        for bench_path, bench_config in Server().benches.iter_configs()
        if bench_config.production.enabled
    ]


class UptimeRunner:
    """Pings the sites of every production bench each round. Datum gets the count of sites in each
    state at most once a minute, though rounds run more often."""

    telemetry_interval_seconds = 60.0

    def __init__(self) -> None:
        self.telemetry_sent_at: float | None = None

    def run(self) -> None:
        """Run one round."""
        monitors = _production_uptime_monitors()
        results = [result for monitor in monitors for result in monitor.collect()]
        if monitors and self.is_telemetry_due:
            self.telemetry_sent_at = time.monotonic()
            shipper = TelemetryShipper(monitors[0].bench.config.telemetry)
            shipper.add_sites(results)
            shipper.send()

    @property
    def is_telemetry_due(self) -> bool:
        """Whether a minute has passed since the last send."""
        if self.telemetry_sent_at is None:
            return True
        return time.monotonic() - self.telemetry_sent_at >= self.telemetry_interval_seconds


if __name__ == "__main__":
    UptimeRunner().run()
