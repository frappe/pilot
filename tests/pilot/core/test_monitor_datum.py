from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pilot.config.telemetry import TelemetryConfig
from pilot.core.server import monitoring_datum
from pilot.core.server.monitoring_datum import TelemetryShipper, repository_name, update_line
from pilot.internal.jsonl_log import JsonlLog

pytest.importorskip("datum_client", reason="datum is installed in the admin venv only")


@pytest.fixture
def log_dir(tmp_path: Path, monkeypatch) -> Path:
    """The monitor log directory, with the Central and update records in it."""
    monkeypatch.setattr(monitoring_datum, "monitor_log_dir", lambda: tmp_path)
    monkeypatch.setattr(
        monitoring_datum, "get_central_request_log", lambda: JsonlLog(tmp_path, "central-requests")
    )
    monkeypatch.setattr(monitoring_datum, "get_update_log", lambda: JsonlLog(tmp_path, "updates"))
    return tmp_path


def _shipper() -> TelemetryShipper:
    return TelemetryShipper(TelemetryConfig(endpoint="https://datum.internal", token="secret"))


def _samples(shipper: TelemetryShipper) -> dict[tuple[str, str], float]:
    """Every sample as {(metric, sorted labels): value}."""
    return {
        (sample["metric"], ",".join(f"{k}={v}" for k, v in sorted(sample["labels"].items()))): sample["value"]
        for batch in shipper.batches
        for sample in batch.samples
    }


def test_nothing_is_sent_until_endpoint_and_token_are_set() -> None:
    shipper = TelemetryShipper(TelemetryConfig(endpoint="https://datum.internal"))

    shipper.add_info()
    shipper.add_filesystems()

    assert not shipper.is_enabled
    assert shipper.batches == []
    assert shipper.send() == 0


def test_nothing_is_sent_without_the_datum_client_package(monkeypatch) -> None:
    monkeypatch.setattr(monitoring_datum, "HAS_DATUM_CLIENT", False)
    shipper = _shipper()

    shipper.add_info()

    assert not shipper.is_enabled
    assert shipper.batches == []


def test_the_heartbeat_names_the_pilot_version() -> None:
    shipper = _shipper()
    shipper.add_info()

    assert _samples(shipper) == {("pilot_info", f"version={monitoring_datum.__version__}"): 1}


def test_the_database_reports_up_or_down() -> None:
    shipper = _shipper()
    shipper.add_database_up(False)

    assert _samples(shipper) == {("pilot_database_up", ""): 0}


def test_disk_space_uses_the_contract_names() -> None:
    shipper = _shipper()
    shipper.add_filesystems()

    assert {metric for metric, _ in _samples(shipper)} == {
        "system_filesystem_size_bytes",
        "system_filesystem_available_bytes",
    }


def test_central_calls_of_the_last_minute_are_counted(log_dir: Path) -> None:
    now = datetime.now(UTC)
    log = JsonlLog(log_dir, "central-requests")
    for seconds, method, result in [
        (90, "report_pilot_event", "failed"),
        (30, "report_pilot_event", "failed"),
        (20, "report_pilot_event", "ok"),
        (10, "datum_token", "ok"),
        (5, "report_pilot_event", "failed"),
    ]:
        log.append(
            {"time": (now - timedelta(seconds=seconds)).isoformat(), "method": method, "result": result}
        )
    shipper = _shipper()

    shipper.add_central_requests(now)

    assert _samples(shipper) == {
        ("pilot_central_requests_last_minute", "method=report_pilot_event,result=failed"): 2,
        ("pilot_central_requests_last_minute", "method=report_pilot_event,result=ok"): 1,
        ("pilot_central_requests_last_minute", "method=datum_token,result=ok"): 1,
    }


def test_metrics_go_in_one_post_and_clear_the_batches() -> None:
    shipper = _shipper()
    shipper.add_info()
    shipper.add_database_up(True)
    sent = []

    shipper.client.send = lambda *batches: sent.append(batches) or 202  # type: ignore[union-attr]

    assert shipper.send() == 202
    assert len(sent) == 1
    assert shipper.batches == []
    assert shipper.send() == 0


FAILED_UPDATE = {
    "time": "2026-11-14T10:00:00+00:00",
    "app": "erpnext",
    "repository_url": "https://github.com/frappe/erpnext.git",
    "from_version": "a1b2c3d4e5f6",
    "to_version": "v15.40.0",
    "status": "failed",
    "phase": "migrating",
    "site": "a.example.com",
    "message": "Patch failed",
    "output_excerpt": "Traceback ...",
}


def test_a_failed_update_becomes_one_error_line() -> None:
    line = update_line(FAILED_UPDATE)

    assert line["level"] == "error"
    assert (line["product"], line["service"], line["source"]) == ("pilot", "update", "event")
    assert line["attributes"]["repository"] == "frappe/erpnext"
    assert line["attributes"]["phase"] == "migrating"
    assert line["message"] == "Patch failed\nTraceback ..."


def test_a_completed_update_has_no_failure_fields() -> None:
    line = update_line(
        {"time": "2026-11-14T10:00:00+00:00", "app": "frappe", "status": "completed", "to_version": "v15.1.0"}
    )

    assert line["level"] == "info"
    assert line["attributes"] == {
        "app": "frappe",
        "repository": "frappe",
        "to_version": "v15.1.0",
        "status": "completed",
    }


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/frappe/erpnext.git",
        "git@github.com:frappe/erpnext.git",
        "https://github.com/frappe/erpnext",
    ],
)
def test_the_repository_is_owner_and_name(url: str) -> None:
    assert repository_name(url) == "frappe/erpnext"


def test_update_events_are_sent_once_and_in_order(log_dir: Path) -> None:
    (log_dir / "updates.sent").write_text("2026-11-14T09:00:00+00:00")
    log = JsonlLog(log_dir, "updates")
    log.append({**FAILED_UPDATE, "time": "2026-11-14T08:00:00+00:00"})
    log.append({**FAILED_UPDATE, "time": "2026-11-14T10:00:00+00:00", "app": "first"})
    log.append({**FAILED_UPDATE, "time": "2026-11-14T11:00:00+00:00", "app": "second"})
    shipper = _shipper()
    sent = []
    shipper.client.send_logs = lambda lines: sent.append(lines) or 200  # type: ignore[union-attr]

    shipper.send_update_events()
    shipper.send_update_events()

    assert [[line["attributes"]["app"] for line in lines] for lines in sent] == [["first", "second"]]
    assert (log_dir / "updates.sent").read_text() == "2026-11-14T11:00:00+00:00"


def test_a_refused_request_keeps_the_events_for_the_next_minute(log_dir: Path) -> None:
    (log_dir / "updates.sent").write_text("2026-11-14T09:00:00+00:00")
    JsonlLog(log_dir, "updates").append(FAILED_UPDATE)
    shipper = _shipper()
    shipper.client.send_logs = lambda lines: 503  # type: ignore[union-attr]

    shipper.send_update_events()

    assert (log_dir / "updates.sent").read_text() == "2026-11-14T09:00:00+00:00"


def test_the_first_run_starts_from_now(log_dir: Path) -> None:
    JsonlLog(log_dir, "updates").append(FAILED_UPDATE)
    shipper = _shipper()
    sent = []
    shipper.client.send_logs = lambda lines: sent.append(lines) or 200  # type: ignore[union-attr]

    shipper.send_update_events()

    assert sent == []
    assert (log_dir / "updates.sent").exists()


def test_site_states_are_counted_with_zeroes() -> None:
    shipper = _shipper()
    shipper.add_sites(
        [
            {"site": "a.local", "up": True},
            {"site": "b.local", "up": False},
            {"site": "c.local", "up": False, "maintenance": True},
            {"site": "d.local", "up": True},
        ]
    )
    samples = _samples(shipper)

    assert samples[("pilot_sites", "state=up")] == 2
    assert samples[("pilot_sites", "state=down")] == 1
    assert samples[("pilot_sites", "state=maintenance")] == 1


def test_no_site_still_reports_every_state() -> None:
    shipper = _shipper()
    shipper.add_sites([])

    assert _samples(shipper)[("pilot_sites", "state=down")] == 0


def test_backups_count_the_newest_run_of_each_site() -> None:
    now = datetime(2026, 11, 14, tzinfo=UTC)
    shipper = _shipper()
    shipper.add_backups(
        [
            {
                "a.local": [(now, {"status": "failed"}), (now, {"status": "success"})],
                "b.local": [(now, {"status": "success"})],
            },
            {"c.local": [(now, {"status": "failed"})], "d.local": []},
        ]
    )
    samples = _samples(shipper)

    assert samples[("pilot_site_backups", "status=failed")] == 2
    assert samples[("pilot_site_backups", "status=success")] == 1
