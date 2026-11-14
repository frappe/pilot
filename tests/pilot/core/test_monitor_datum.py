from datetime import UTC, datetime

import pytest

from pilot.config.telemetry import TelemetryConfig
from pilot.core.server import monitoring_datum
from pilot.core.server.monitoring_datum import MetricShipper

pytest.importorskip("datum_client", reason="datum is installed in the admin venv only")

WHEN = "2026-08-04T10:00:00+00:00"

# The machine metrics of the telemetry contract, which every machine sends with these names.
MACHINE_METRICS = {
    "system_boot_time_seconds",
    "system_cpu_count",
    "system_cpu_seconds_total",
    "system_load_average",
    "system_memory_total_bytes",
    "system_memory_available_bytes",
    "system_memory_swap_used_bytes",
    "system_filesystem_size_bytes",
    "system_filesystem_available_bytes",
    "system_disk_read_bytes_total",
    "system_disk_written_bytes_total",
    "system_network_receive_bytes_total",
    "system_network_transmit_bytes_total",
    "system_network_receive_errors_total",
    "system_network_transmit_errors_total",
}

APPLICATION_RECORD = {
    "time": WHEN,
    "bench": "bench_0001",
    "processes": [
        {
            "service": "bench_0001-web.service",
            "pid": 42,
            "state": "sleeping",
            "cpu_percent": 1.2,
            "memory_rss_bytes": 4 * 1024**2,
            "read_bytes": 4096,
            "write_bytes": 2048,
            "open_fds": 30,
        },
        {"service": "bench_0001-worker.service", "pid": 43, "missing": True},
    ],
}

DATABASE_RECORD = {
    "time": WHEN,
    "Com_insert": 10,
    "Com_update": 5,
    "Com_delete": 1,
    "Com_select": 100,
    "Questions": 200,
    "Innodb_buffer_pool_reads": 7,
    "Innodb_buffer_pool_read_requests": 700,
    "Innodb_row_lock_time": 2500,
    "Innodb_row_lock_waits": 3,
    "Threads_connected": 9,
    "innodb_buffer_pool_size": 1024**3,
    "max_connections": 151,
    "total_ram_bytes": 8 * 1024**3,
}


def _shipper() -> MetricShipper:
    return MetricShipper(TelemetryConfig(endpoint="https://datum.internal", token="secret"))


def _samples(shipper: MetricShipper) -> dict[tuple[str, str], float]:
    """Every sample as {(metric, sorted labels): value}."""
    return {
        (sample["metric"], ",".join(f"{k}={v}" for k, v in sorted(sample["labels"].items()))): sample["value"]
        for batch in shipper.batches
        for sample in batch.samples
    }


def test_shipping_is_off_until_endpoint_and_token_are_set() -> None:
    shipper = MetricShipper(TelemetryConfig(endpoint="https://datum.internal"))

    shipper.add_machine()
    shipper.add_application(APPLICATION_RECORD)
    shipper.add_database(DATABASE_RECORD)

    assert not shipper.is_enabled
    assert shipper.batches == []
    assert shipper.send() == 0


def test_shipping_is_off_without_the_datum_client_package(monkeypatch) -> None:
    monkeypatch.setattr(monitoring_datum, "HAS_DATUM_CLIENT", False)
    shipper = _shipper()

    shipper.add_machine()

    assert not shipper.is_enabled
    assert shipper.batches == []


def test_machine_metrics_follow_the_contract() -> None:
    shipper = _shipper()
    shipper.add_machine()
    samples = [sample for batch in shipper.batches for sample in batch.samples]

    assert {sample["metric"] for sample in samples} == MACHINE_METRICS
    cpu = [sample["labels"] for sample in samples if sample["metric"] == "system_cpu_seconds_total"]
    assert {"idle", "user", "iowait"} <= {labels["mode"] for labels in cpu}
    assert all(set(labels) == {"cpu", "mode"} for labels in cpu)
    assert ("system_load_average", "window=5m") in _samples(shipper)


def test_application_metrics_carry_bench_and_service_labels() -> None:
    shipper = _shipper()
    shipper.add_application(APPLICATION_RECORD)
    samples = _samples(shipper)
    web = "bench=bench_0001,service=bench_0001-web.service"

    assert samples[("pilot_process_up", web)] == 1.0
    assert samples[("pilot_process_cpu_percent", web)] == 1.2
    assert samples[("pilot_process_memory_rss_bytes", web)] == 4 * 1024**2
    assert samples[("pilot_process_io_read_bytes_total", web)] == 4096
    assert samples[("pilot_process_state", f"{web},state=sleeping")] == 1.0
    assert samples[("pilot_process_open_fds", web)] == 30


def test_a_missing_process_reports_zero_rather_than_nothing() -> None:
    shipper = _shipper()
    shipper.add_application(APPLICATION_RECORD)
    samples = _samples(shipper)
    worker = "bench=bench_0001,service=bench_0001-worker.service"

    assert samples[("pilot_process_up", worker)] == 0.0
    assert ("pilot_process_cpu_percent", worker) not in samples


def test_pid_is_never_a_label() -> None:
    shipper = _shipper()
    shipper.add_application(APPLICATION_RECORD)

    assert not [
        sample for batch in shipper.batches for sample in batch.samples if "pid" in sample["labels"]
    ]


def test_database_counters_ship_as_counters() -> None:
    shipper = _shipper()
    shipper.add_database(DATABASE_RECORD)
    samples = _samples(shipper)

    assert samples[("mariadb_queries_total", "kind=select")] == 100
    assert samples[("mariadb_questions_total", "")] == 200
    assert samples[("mariadb_row_lock_wait_seconds_total", "")] == 2.5
    assert samples[("mariadb_connections", "")] == 9
    assert samples[("mariadb_buffer_pool_size_bytes", "")] == 1024**3


def test_host_memory_is_not_shipped_twice() -> None:
    """The host's RAM is a system fact; MariaDB's sample must not repeat it."""
    shipper = _shipper()
    shipper.add_database(DATABASE_RECORD)

    assert not [name for name, _ in _samples(shipper) if "memory" in name]


def test_database_metrics_are_skipped_when_the_sample_failed() -> None:
    shipper = _shipper()
    shipper.add_database(None)

    assert shipper.batches == []


def test_all_machine_metrics_of_one_reading_share_one_time() -> None:
    shipper = _shipper()
    shipper.add_machine()

    assert len({sample["ts"] for batch in shipper.batches for sample in batch.samples}) == 1


def test_a_tick_is_one_post_and_clears_the_batches() -> None:
    shipper = _shipper()
    shipper.add_machine()
    shipper.add_application(APPLICATION_RECORD)
    sent = []

    shipper.client.send = lambda *batches: sent.append(batches) or 202  # type: ignore[union-attr]

    assert shipper.send() == 202
    assert len(sent) == 1
    assert shipper.batches == []
    assert shipper.send() == 0


def test_a_record_without_a_usable_time_still_ships() -> None:
    """An unreadable timestamp falls back to now, rather than dropping the tick."""
    shipper = _shipper()

    shipper.add_application({**APPLICATION_RECORD, "time": "not-a-timestamp"})
    shipper.add_application({**APPLICATION_RECORD, "time": None})
    shipper.add_database({**DATABASE_RECORD})

    assert all(sample["ts"] for batch in shipper.batches for sample in batch.samples)


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
