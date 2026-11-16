"""Tests for the site-down incident alerts raised by the uptime monitor."""

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import BenchConfig, MariaDBConfig, RedisConfig, WorkerConfig
from pilot.config.webhook import WebhookEndpoint
from pilot.core.bench import Bench
from pilot.core.notification.webhooks import WebhookDelivery
from pilot.core.site.uptime_monitoring import (
    UptimeMonitor,
    UptimeRunner,
    get_failure_reason,
    get_ping_fault,
)


def _accept(record):
    def send(endpoint, body):
        record(endpoint, body)
        return WebhookDelivery(200, "")

    return send


def _monitor(tmp_path: Path, *, alerting: bool = True, webhooks: dict | None = None) -> UptimeMonitor:
    config = BenchConfig(
        name="my-bench",
        python_version="3.14",
        mariadb=MariaDBConfig(),
        redis=RedisConfig(),
        workers=WorkerConfig(),
    )
    config.resource_limits.site_uptime = alerting
    webhooks = webhooks or {"https://alerts.example.com": "tok"}
    config.webhook_endpoints = [
        WebhookEndpoint(url, token) for url, token in webhooks.items()
    ]
    monitor = UptimeMonitor(Bench(config, tmp_path / "my-bench"))
    monitor.alerts_path.parent.mkdir(parents=True, exist_ok=True)
    return monitor


def _ping(site: str, up: bool) -> dict:
    return {
        "time": "2026-08-10T12:00:00+00:00",
        "site": site,
        "up": up,
        "status_code": 200 if up else 502,
        "response_ms": 12,
    }


def _age_alerts(monitor: UptimeMonitor) -> None:
    """Push every alert past its window, whether it waits to fire or to resolve."""
    state = json.loads(monitor.alerts_path.read_text())
    for entry in state.values():
        for field in ("since", "clear_since"):
            if field in entry:
                entry[field] -= monitor.alert_window_seconds + 1
    monitor.alerts_path.write_text(json.dumps(state))


def test_a_single_failed_ping_is_not_an_incident(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    sent = []

    with patch("pilot.core.notification.webhooks.send_webhook", _accept(lambda *args: sent.append(args))):
        monitor.send_alert_if_required([_ping("a.test", up=False)])

    assert sent == []
    assert list(json.loads(monitor.alerts_path.read_text())) == ["a.test"]


def test_a_site_down_for_the_whole_window_alerts_once(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    monitor.send_alert_if_required([_ping("a.test", up=False)])
    _age_alerts(monitor)
    payloads = []

    with patch(
        "pilot.core.notification.webhooks.send_webhook", _accept(lambda endpoint, body: payloads.append(body))
    ):
        monitor.send_alert_if_required([_ping("a.test", up=False)])
        monitor.send_alert_if_required([_ping("a.test", up=False)])

    assert len(payloads) == 1
    assert payloads[0]["event"] == "site_down"
    assert payloads[0]["message"] == "Health check failed for 60 seconds (HTTP 502)."
    assert payloads[0]["context"]["sites"] == [
        {"site": "a.test", "status_code": 502, "response_ms": 12, "error": None}
    ]


def test_a_site_down_for_one_minute_alerts(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    monitor.send_alert_if_required([_ping("a.test", up=False)])
    state = json.loads(monitor.alerts_path.read_text())
    state["a.test"]["since"] -= 61
    monitor.alerts_path.write_text(json.dumps(state))
    payloads = []

    with patch(
        "pilot.core.notification.webhooks.send_webhook", _accept(lambda endpoint, body: payloads.append(body))
    ):
        monitor.send_alert_if_required([_ping("a.test", up=False)])

    assert [payload["context"]["sustained_seconds"] for payload in payloads] == [60]


def test_only_the_sites_still_down_are_reported(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    monitor.send_alert_if_required([_ping("a.test", up=False), _ping("b.test", up=False)])
    _age_alerts(monitor)
    payloads = []

    with patch(
        "pilot.core.notification.webhooks.send_webhook", _accept(lambda endpoint, body: payloads.append(body))
    ):
        monitor.send_alert_if_required([_ping("a.test", up=False), _ping("b.test", up=True)])

    assert [site["site"] for site in payloads[0]["context"]["sites"]] == ["a.test"]
    assert list(json.loads(monitor.alerts_path.read_text())) == ["a.test"]


def test_every_site_recovering_removes_the_alerts_file(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    monitor.send_alert_if_required([_ping("a.test", up=False)])

    monitor.send_alert_if_required([_ping("a.test", up=True)])

    assert not monitor.alerts_path.exists()


def test_alerts_go_only_to_the_configured_endpoints(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path, webhooks={"https://one.test": "a", "https://two.test": "b"})
    monitor.send_alert_if_required([_ping("a.test", up=False)])
    _age_alerts(monitor)
    reached = []

    with patch(
        "pilot.core.notification.webhooks.send_webhook",
        _accept(lambda endpoint, body: reached.append(endpoint.url)),
    ):
        monitor.send_alert_if_required([_ping("a.test", up=False)])

    assert reached == ["https://one.test", "https://two.test"]


def test_no_alerts_when_uptime_alerting_is_switched_off(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path, alerting=False)
    sent = []

    with patch("pilot.core.notification.webhooks.send_webhook", _accept(lambda *args: sent.append(args))):
        monitor.send_alert_if_required([_ping("a.test", up=False)])
        _age = monitor.alerts_path.exists()
        monitor.send_alert_if_required([_ping("a.test", up=False)])

    assert sent == []
    assert not _age, "a disabled alert keeps no window state to go stale"


def test_collect_logs_every_ping_and_then_alerts(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    monitor._configurator.log_path.parent.mkdir(parents=True, exist_ok=True)

    with (
        patch.object(UptimeMonitor, "get_sites", return_value=["a.test"]),
        patch.object(UptimeMonitor, "get_maintenance_sites", return_value=set()),
        patch.object(UptimeMonitor, "ping_site", return_value=_ping("a.test", up=False)),
    ):
        monitor.collect()

    logged = json.loads(monitor._configurator.log_path.read_text().splitlines()[-1])
    assert logged["site"] == "a.test"
    assert list(json.loads(monitor.alerts_path.read_text())) == ["a.test"]


def test_a_site_in_maintenance_never_alerts(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    monitor._configurator.log_path.parent.mkdir(parents=True, exist_ok=True)
    sent = []

    with (
        patch.object(UptimeMonitor, "get_sites", return_value=["a.test"]),
        patch.object(UptimeMonitor, "get_maintenance_sites", return_value={"a.test"}),
        patch.object(UptimeMonitor, "ping_site", side_effect=lambda site: _ping(site, up=False)),
        patch("pilot.core.notification.webhooks.send_webhook", _accept(lambda *args: sent.append(args))),
    ):
        monitor.collect()

    logged = json.loads(monitor._configurator.log_path.read_text().splitlines()[-1])
    assert logged["maintenance"] is True
    assert sent == []
    assert not monitor.alerts_path.exists() or json.loads(monitor.alerts_path.read_text()) == {}


def test_an_undelivered_alert_is_retried_on_the_next_tick(tmp_path: Path) -> None:
    """Every sink being down is not a delivered alert, so the incident must not
    be marked notified and silently forgotten."""
    monitor = _monitor(tmp_path)
    monitor.send_alert_if_required([_ping("a.test", up=False)])
    _age_alerts(monitor)

    def refuse(endpoint, body):
        raise OSError("connection refused")

    with patch("pilot.core.notification.webhooks.send_webhook", refuse):
        monitor.send_alert_if_required([_ping("a.test", up=False)])

    assert json.loads(monitor.alerts_path.read_text())["a.test"]["sent"] is False

    delivered = []
    with patch(
        "pilot.core.notification.webhooks.send_webhook",
        _accept(lambda endpoint, body: delivered.append(endpoint.url)),
    ):
        monitor.send_alert_if_required([_ping("a.test", up=False)])

    assert delivered == ["https://alerts.example.com"]
    assert json.loads(monitor.alerts_path.read_text())["a.test"]["sent"] is True


def test_a_site_that_stays_down_is_recorded_locally_once(tmp_path: Path) -> None:
    """The retry above repeats every five seconds. Only the delivery repeats -
    the bench's own feed keeps one row for the incident."""
    monitor = _monitor(tmp_path)
    monitor.send_alert_if_required([_ping("a.test", up=False)])
    _age_alerts(monitor)

    def refuse(endpoint, body):
        raise OSError("connection refused")

    with patch("pilot.core.notification.webhooks.send_webhook", refuse):
        for _ in range(5):
            monitor.send_alert_if_required([_ping("a.test", up=False)])

    recorded = monitor.bench.notifications.list(10)
    assert [item.title for item in recorded] == ["a.test is unreachable"]


def test_a_second_site_going_down_gets_its_own_record(tmp_path: Path) -> None:
    """Recording is per condition, so a new site joining an existing incident is
    not swallowed by the first site's record."""
    monitor = _monitor(tmp_path)
    monitor.send_alert_if_required([_ping("a.test", up=False)])
    _age_alerts(monitor)

    with patch("pilot.core.notification.webhooks.send_webhook", _accept(lambda endpoint, body: None)):
        monitor.send_alert_if_required([_ping("a.test", up=False)])
        monitor.send_alert_if_required([_ping("a.test", up=False), _ping("b.test", up=False)])
        _age_alerts(monitor)
        monitor.send_alert_if_required([_ping("a.test", up=False), _ping("b.test", up=False)])

    titles = sorted(item.title for item in monitor.bench.notifications.list(10))
    assert titles == ["a.test is unreachable", "b.test is unreachable"]


class _AppServer(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.server.requests.append(self.headers["X-Frappe-Site-Name"])
        self.send_response(self.server.status)
        self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/redirected")
        self.send_header("Content-Length", str(len(self.server.body)))
        self.end_headers()
        self.wfile.write(self.server.body)

    def log_message(self, *args) -> None:
        pass


@contextmanager
def _app_server(monitor: UptimeMonitor, status: int, body: bytes = b"") -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AppServer)
    server.status, server.body, server.requests = status, body, []
    monitor.bench.config.http_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_a_ping_reaches_the_site_on_the_local_app_server(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    with _app_server(monitor, 200) as server:
        result = monitor.ping_site("a.test")

    assert server.requests == ["a.test"]
    assert result["up"] is True


def test_a_ping_does_not_follow_a_redirect(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    with _app_server(monitor, 301) as server:
        result = monitor.ping_site("a.test")

    assert server.requests == ["a.test"]
    assert result["up"] is False
    assert result["status_code"] == 301


def test_a_ping_ignores_a_proxy_in_the_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monitor = _monitor(tmp_path)
    with _app_server(monitor, 200):
        assert monitor.ping_site("a.test")["up"] is True


def test_a_ping_without_an_http_answer_keeps_its_reason(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    with _app_server(monitor, 200):
        pass

    result = monitor.ping_site("a.test")

    assert result["up"] is False
    assert result["status_code"] is None
    assert result["fault"] == "platform"
    assert "refused" in result["error"].lower()


def test_an_http_failure_has_no_extra_error_field(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    with _app_server(monitor, 502):
        result = monitor.ping_site("a.test")

    assert result["status_code"] == 502
    assert "error" not in result


def test_an_exception_in_app_code_is_an_app_fault(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    with _app_server(monitor, 500, json.dumps({"exc_type": "ImportError"}).encode()):
        result = monitor.ping_site("a.test")

    assert result["up"] is False
    assert result["fault"] == "app"


@pytest.mark.parametrize(
    ("status", "body", "fault"),
    [
        (403, b"", "app"),
        (429, b"", "app"),
        (404, b"", "platform"),
        (301, b"", "platform"),
        (502, b"", "platform"),
        (500, b'{"exc_type": "ValidationError"}', "app"),
        (500, b'{"exc_type": "OperationalError"}', "platform"),
        (500, b'{"exc_type": "ConnectionError"}', "platform"),
        (500, b"<html>Internal Server Error</html>", "platform"),
        (500, b"[]", "platform"),
        (500, b"{}", "platform"),
    ],
)
def test_a_failed_ping_is_the_fault_of_app_code_or_of_the_platform(
    status: int, body: bytes, fault: str
) -> None:
    assert get_ping_fault(status, body) == fault


def test_a_site_down_alert_is_about_the_site(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)

    payload = monitor.get_down_payload(_ping("a.test", up=False))

    assert (payload["title"], payload["site"], payload["action_route"]) == (
        "a.test is unreachable",
        "a.test",
        "/sites/a.test",
    )
    assert (payload["reference_doctype"], payload["reference_name"]) == ("Site", "a.test")
    assert payload["severity"] == "Error" and payload["category"] == "Sites"


def test_a_ping_with_no_answer_names_the_error() -> None:
    assert get_failure_reason({"status_code": None, "error": "connection refused"}) == "connection refused"
    assert get_failure_reason({"status_code": 500}) == "HTTP 500"


def _fire(monitor: UptimeMonitor, send) -> None:
    """Take a.test from up to a delivered site_down alert."""
    monitor.send_alert_if_required([_ping("a.test", up=False)])
    _age_alerts(monitor)
    with patch("pilot.core.notification.webhooks.send_webhook", send):
        monitor.send_alert_if_required([_ping("a.test", up=False)])


def test_a_site_that_answers_for_the_whole_window_sends_site_up(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    payloads = []
    record = _accept(lambda endpoint, body: payloads.append(body))
    _fire(monitor, record)

    with patch("pilot.core.notification.webhooks.send_webhook", record):
        monitor.send_alert_if_required([_ping("a.test", up=True)])
        assert [body["event"] for body in payloads] == ["site_down"], "one answer does not resolve it"
        _age_alerts(monitor)
        monitor.send_alert_if_required([_ping("a.test", up=True)])
        monitor.send_alert_if_required([_ping("a.test", up=True)])

    assert [body["event"] for body in payloads] == ["site_down", "site_up"]
    assert payloads[1]["site"] == "a.test"
    assert payloads[1]["severity"] == "Info"
    assert not monitor.alerts_path.exists()
    titles = [item.title for item in monitor.bench.notifications.list(10)]
    assert titles == ["a.test is reachable again", "a.test is unreachable"]


def test_a_site_that_fails_again_while_recovering_stays_firing(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    payloads = []
    record = _accept(lambda endpoint, body: payloads.append(body))
    _fire(monitor, record)

    with patch("pilot.core.notification.webhooks.send_webhook", record):
        monitor.send_alert_if_required([_ping("a.test", up=True)])
        _age_alerts(monitor)
        monitor.send_alert_if_required([_ping("a.test", up=False)])
        monitor.send_alert_if_required([_ping("a.test", up=True)])

    assert [body["event"] for body in payloads] == ["site_down"], "the clear window starts over"


def test_an_unsent_site_up_is_retried_on_the_next_tick(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    payloads = []
    _fire(monitor, _accept(lambda endpoint, body: payloads.append(body)))
    monitor.send_alert_if_required([_ping("a.test", up=True)])
    _age_alerts(monitor)

    def refuse(endpoint, body):
        raise OSError("connection refused")

    with patch("pilot.core.notification.webhooks.send_webhook", refuse):
        monitor.send_alert_if_required([_ping("a.test", up=True)])
    with patch(
        "pilot.core.notification.webhooks.send_webhook", _accept(lambda endpoint, body: payloads.append(body))
    ):
        monitor.send_alert_if_required([_ping("a.test", up=True)])

    assert [body["event"] for body in payloads] == ["site_down", "site_up"]


def test_a_site_in_maintenance_keeps_its_alert_firing(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    payloads = []
    record = _accept(lambda endpoint, body: payloads.append(body))
    _fire(monitor, record)
    in_maintenance = {**_ping("a.test", up=False), "maintenance": True}

    with patch("pilot.core.notification.webhooks.send_webhook", record):
        monitor.send_alert_if_required([in_maintenance])
        _age_alerts(monitor)
        monitor.send_alert_if_required([in_maintenance])

    assert [body["event"] for body in payloads] == ["site_down"]
    assert json.loads(monitor.alerts_path.read_text())["a.test"]["status"] == "firing"


def test_a_deleted_site_drops_its_alert_without_site_up(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    payloads = []
    record = _accept(lambda endpoint, body: payloads.append(body))
    _fire(monitor, record)

    with patch("pilot.core.notification.webhooks.send_webhook", record):
        monitor.send_alert_if_required([_ping("b.test", up=True)])

    assert [body["event"] for body in payloads] == ["site_down"]
    assert not monitor.alerts_path.exists()


def test_an_alerts_file_in_the_older_format_starts_over(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    monitor.alerts_path.write_text(json.dumps({"a.test": {"since": 0, "notified": True}}))
    payloads = []

    with patch(
        "pilot.core.notification.webhooks.send_webhook", _accept(lambda endpoint, body: payloads.append(body))
    ):
        monitor.send_alert_if_required([_ping("a.test", up=False)])

    assert payloads == []
    assert json.loads(monitor.alerts_path.read_text())["a.test"]["status"] == "pending"


def test_the_runner_sends_site_states_to_datum_once_a_minute() -> None:
    runner = UptimeRunner()
    monitor = type("Monitor", (), {"collect": lambda self: [_ping("a.test", up=True)]})()
    monitor.bench = type("Bench", (), {"config": type("Config", (), {"telemetry": None})()})()

    with (
        patch("pilot.core.site.uptime_monitoring._production_uptime_monitors", return_value=[monitor]),
        patch("pilot.core.site.uptime_monitoring.TelemetryShipper") as shipper,
        patch("pilot.core.site.uptime_monitoring.time.monotonic") as monotonic,
    ):
        for now in (0.0, 15.0, 30.0, 60.0):
            monotonic.return_value = now
            runner.run()

    assert shipper.return_value.send.call_count == 2


def test_a_site_that_recovers_before_any_alert_sends_nothing(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    payloads = []

    with patch(
        "pilot.core.notification.webhooks.send_webhook", _accept(lambda endpoint, body: payloads.append(body))
    ):
        monitor.send_alert_if_required([_ping("a.test", up=False)])
        monitor.send_alert_if_required([_ping("a.test", up=True)])

    assert payloads == []


def test_a_site_still_being_created_is_not_pinged(tmp_path: Path) -> None:
    monitor = _monitor(tmp_path)
    sites = [
        type("S", (), {"config": type("C", (), {"name": name})()})() for name in ("ready.local", "new.local")
    ]

    with (
        patch.object(Bench, "sites", return_value=sites),
        patch(
            "pilot.core.site.uptime_monitoring.TaskReader.get_provisioning_site_names",
            return_value={"new.local"},
        ),
    ):
        assert monitor.get_sites() == ["ready.local"]
