from datetime import UTC, datetime

import pytest

from pilot.config.telemetry import TelemetryConfig
from pilot.core.server import monitoring_datum
from pilot.core.server.monitoring_datum import MetricShipper

pytest.importorskip("datum_client", reason="datum is installed in the admin venv only")

WHEN = "2026-08-04T10:00:00+00:00"

SYSTEM_RECORD = {
    "time": WHEN,
    "load_avg": [0.5, 0.4, 0.3],
    "cpu_percent": 12.5,
    "cpu_breakdown": {"user": 8.0, "system": 4.5, "idle": 87.5},
    "memory": {
        "total_bytes": 8 * 1024**3,
        "used_bytes": 4 * 1024**3,
        "cached_bytes": 1024**3,
        "free_bytes": 3 * 1024**3,
        "swap_used_bytes": 0,
        "percent": 50.0,
    },
    "storage": {
        "disk": {"total_bytes": 50 * 1024**3, "used_bytes": 20 * 1024**3, "free_bytes": 30 * 1024**3, "percent": 40.0}
    },
    "network": {"rx_bytes_per_sec": 2000.0, "tx_bytes_per_sec": 500.0},
    "disk_io": {"read_bytes_per_sec": 1000.0, "write_bytes_per_sec": 500.0},
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

    shipper.add_system(SYSTEM_RECORD)
    shipper.add_application(APPLICATION_RECORD)
    shipper.add_database(DATABASE_RECORD)

    assert not shipper.is_enabled
    assert shipper.batches == []
    assert shipper.send() == 0


def test_shipping_is_off_without_the_datum_client_package(monkeypatch) -> None:
    monkeypatch.setattr(monitoring_datum, "HAS_DATUM_CLIENT", False)
    shipper = _shipper()

    shipper.add_system(SYSTEM_RECORD)

    assert not shipper.is_enabled
    assert shipper.batches == []


def test_system_metrics_are_named_in_base_units() -> None:
    shipper = _shipper()
    shipper.add_system(SYSTEM_RECORD)
    samples = _samples(shipper)

    assert samples[("system_cpu_usage_percent", "")] == 12.5
    assert samples[("system_cpu_mode_percent", "mode=user")] == 8.0
    assert samples[("system_load_average", "window=5m")] == 0.4
    assert samples[("system_memory_used_bytes", "")] == 4 * 1024**3
    assert samples[("system_disk_total_bytes", "")] == 50 * 1024**3
    assert samples[("system_disk_usage_percent", "")] == 40.0
    assert samples[("system_network_receive_bytes_per_second", "")] == 2000.0
    assert samples[("system_disk_write_bytes_per_second", "")] == 500.0
    assert not [name for name, _ in samples if "_mb" in name or "_kb" in name]


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


def test_every_sample_uses_the_time_the_reading_was_taken() -> None:
    shipper = _shipper()
    shipper.add_system(SYSTEM_RECORD)

    taken = datetime.fromisoformat(WHEN).astimezone(UTC).isoformat().replace("+00:00", "Z")
    assert {sample["ts"] for batch in shipper.batches for sample in batch.samples} == {taken}


def test_a_tick_is_one_post_and_clears_the_batches() -> None:
    shipper = _shipper()
    shipper.add_system(SYSTEM_RECORD)
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

    shipper.add_system({**SYSTEM_RECORD, "time": "not-a-timestamp"})
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
