import json
import urllib.error
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import psutil
import pytest

from pilot.config import BenchConfig, MariaDBConfig, RedisConfig, WorkerConfig
from pilot.core.alerts import ALERT_SUSTAINED_SECONDS, ALERT_TIMEOUT_SECONDS, notify, send_alert
from pilot.core.bench import Bench
from pilot.core.server.monitoring import Monitor, MonitorConfigurator
from pilot.core.server.monitoring_proc import CPU_STAT_FIELDS


@pytest.fixture(autouse=True)
def _confine_monitor_logs_to_tmp_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """log_path/system_log_path/etc. are computed from cli_root() with no
    override left - pin it to tmp_path so tests never touch the real host's
    system/logs/ directory."""
    monkeypatch.setattr("pilot.utils.cli_root", lambda: tmp_path)


def _make_bench(path: Path, name: str = "my-bench") -> Bench:
    config = BenchConfig(
        name=name,
        python_version="3.14",
        mariadb=MariaDBConfig(),
        redis=RedisConfig(),
        workers=WorkerConfig(),
    )
    return Bench(config, path)


def _make_monitor(bench: Bench) -> Monitor:
    with patch.object(MonitorConfigurator, "setup"):
        return Monitor(bench)


def _fake_proc_reads(monitor: Monitor) -> None:
    """Stub out /proc reads so tests don't depend on the host machine state."""
    monitor._load_average = lambda: (0.5, 0.4, 0.3)  # type: ignore[method-assign]
    monitor._system_cpu = 12.5
    monitor._memory_usage = lambda: {
        "total_bytes": 8192 * 1024**2,
        "used_bytes": 4096 * 1024**2,
        "available_bytes": 4096 * 1024**2,
        "percent": 50.0,
    }  # type: ignore[method-assign]
    monitor._storage_usage = lambda: {
        "disk": {
            "total_bytes": 51200 * 1024**2,
            "used_bytes": 20480 * 1024**2,
            "free_bytes": 30720 * 1024**2,
            "percent": 40.0,
        }
    }  # type: ignore[method-assign]


def test_collect_system_metrics_writes_to_system_log_file(tmp_path: Path) -> None:
    system_log_file = tmp_path / "system-stats.log"
    monitor = _make_monitor(_make_bench(tmp_path / "my-bench"))
    _fake_proc_reads(monitor)

    with patch.object(type(monitor), "system_log_path", new_callable=PropertyMock, return_value=system_log_file):
        monitor.collect_system_metrics()

    assert system_log_file.exists()
    entry = json.loads(system_log_file.read_text().splitlines()[-1])
    assert entry["load_avg"] == [0.5, 0.4, 0.3]
    assert entry["cpu_percent"] == 12.5
    assert entry["memory"]["percent"] == 50.0


def test_collect_system_metrics_does_not_write_app_log(tmp_path: Path) -> None:
    """System metrics must never bleed into the per-bench application log."""
    system_log_file = tmp_path / "system-stats.log"
    monitor = _make_monitor(_make_bench(tmp_path / "my-bench"))
    _fake_proc_reads(monitor)

    with patch.object(type(monitor), "system_log_path", new_callable=PropertyMock, return_value=system_log_file):
        monitor.collect_system_metrics()

    assert not monitor.log_path.exists()


def test_collect_system_metrics_includes_storage(tmp_path: Path) -> None:
    system_log_file = tmp_path / "system-stats.log"
    monitor = _make_monitor(_make_bench(tmp_path / "my-bench"))
    _fake_proc_reads(monitor)

    with patch.object(type(monitor), "system_log_path", new_callable=PropertyMock, return_value=system_log_file):
        monitor.collect_system_metrics()

    entry = json.loads(system_log_file.read_text().splitlines()[-1])
    assert "storage" in entry
    assert "disk" in entry["storage"]
    assert entry["storage"]["disk"]["percent"] == 40.0


def test_disk_usage_returns_expected_fields(tmp_path: Path) -> None:
    monitor = _make_monitor(_make_bench(tmp_path))
    result = monitor._disk_usage(tmp_path)
    assert result["total_bytes"] > 0
    assert result["used_bytes"] >= 0
    assert result["free_bytes"] >= 0
    assert 0.0 <= result["percent"] <= 100.0
    assert result["total_bytes"] == result["used_bytes"] + result["free_bytes"]


def test_storage_usage_always_includes_disk(tmp_path: Path) -> None:
    monitor = _make_monitor(_make_bench(tmp_path))
    result = monitor._storage_usage()
    assert "disk" in result
    assert result["disk"]["total_bytes"] > 0


def test_compute_cpu_breakdown_sums_to_100_percent(tmp_path: Path) -> None:
    monitor = _make_monitor(_make_bench(tmp_path))
    readings = iter(
        [
            {
                "user": 100,
                "nice": 0,
                "system": 50,
                "idle": 800,
                "iowait": 20,
                "irq": 10,
                "softirq": 10,
                "steal": 10,
            },
            {
                "user": 150,
                "nice": 0,
                "system": 70,
                "idle": 900,
                "iowait": 25,
                "irq": 12,
                "softirq": 12,
                "steal": 11,
            },
        ]
    )
    monitor._cpu_fields = lambda: next(readings)  # type: ignore[method-assign]
    monitor.sample_cpu()
    monitor.compute_cpu()

    breakdown = monitor._cpu_breakdown
    assert set(breakdown) == {"user", "system", "iowait", "irq", "other", "idle"}
    assert abs(sum(breakdown.values()) - 100.0) < 0.5
    assert monitor._system_cpu == round(100 - breakdown["idle"], 2)


def test_compute_cpu_breakdown_zero_delta_reports_idle(tmp_path: Path) -> None:
    """A stalled /proc/stat (identical before/after) must not divide by zero."""
    monitor = _make_monitor(_make_bench(tmp_path))
    fields = {
        "user": 100,
        "nice": 0,
        "system": 50,
        "idle": 800,
        "iowait": 20,
        "irq": 10,
        "softirq": 10,
        "steal": 10,
    }
    monitor._cpu_fields = lambda: dict(fields)  # type: ignore[method-assign]
    monitor.sample_cpu()
    monitor.compute_cpu()

    assert monitor._cpu_breakdown["idle"] == 100.0
    assert monitor._system_cpu == 0.0


def test_memory_usage_breakdown_sums_to_total(tmp_path: Path) -> None:
    monitor = _make_monitor(_make_bench(tmp_path))
    result = monitor._memory_usage()

    assert set(result) >= {
        "total_bytes",
        "used_bytes",
        "cached_bytes",
        "free_bytes",
        "swap_used_bytes",
        "percent",
    }
    assert (
        result["total_bytes"] == result["used_bytes"] + result["cached_bytes"] + result["free_bytes"]
    )


def test_compute_io_reports_bytes_per_sec(tmp_path: Path) -> None:
    monitor = _make_monitor(_make_bench(tmp_path))
    net_readings = iter([{"rx_bytes": 1000, "tx_bytes": 200}, {"rx_bytes": 3000, "tx_bytes": 700}])
    disk_readings = iter(
        [{"read_bytes": 5000, "write_bytes": 1000}, {"read_bytes": 6000, "write_bytes": 1500}]
    )
    monitor._net_fields = lambda: next(net_readings)  # type: ignore[method-assign]
    monitor._disk_io_fields = lambda: next(disk_readings)  # type: ignore[method-assign]

    monitor.sample_io()
    monitor.compute_io()

    assert monitor._network == {"rx_bytes_per_sec": 2000.0, "tx_bytes_per_sec": 500.0}
    assert monitor._disk_io == {"read_bytes_per_sec": 1000.0, "write_bytes_per_sec": 500.0}


def _disk_counter(read_bytes: int, write_bytes: int) -> SimpleNamespace:
    return SimpleNamespace(read_bytes=read_bytes, write_bytes=write_bytes)


def _net_counter(bytes_recv: int, bytes_sent: int) -> SimpleNamespace:
    return SimpleNamespace(bytes_recv=bytes_recv, bytes_sent=bytes_sent)


def test_disk_io_fields_ignores_partitions(tmp_path: Path) -> None:
    """Partitions and dm-/loop devices double-count the disk underneath them."""
    monitor = _make_monitor(_make_bench(tmp_path))
    counters = {
        "sda": _disk_counter(2000, 1000),
        "sda1": _disk_counter(800, 400),
        "nvme0n1": _disk_counter(200, 100),
        "dm-0": _disk_counter(9999, 9999),
        "loop0": _disk_counter(5555, 5555),
    }
    with patch("psutil.disk_io_counters", return_value=counters):
        result = monitor._disk_io_fields()

    assert result == {"read_bytes": 2000 + 200, "write_bytes": 1000 + 100}


def test_net_fields_excludes_loopback(tmp_path: Path) -> None:
    monitor = _make_monitor(_make_bench(tmp_path))
    counters = {
        "eth0": _net_counter(1000, 200),
        "lo": _net_counter(9999, 9999),
        "lo0": _net_counter(8888, 8888),
    }
    with patch("psutil.net_io_counters", return_value=counters):
        result = monitor._net_fields()

    assert result == {"rx_bytes": 1000, "tx_bytes": 200}


def test_cpu_fields_defaults_absent_states_to_zero(tmp_path: Path) -> None:
    """macOS reports no iowait/irq/softirq/steal; the sample must still complete."""
    monitor = _make_monitor(_make_bench(tmp_path))
    times = SimpleNamespace(user=100.0, nice=1.0, system=50.0, idle=800.0)
    with patch("psutil.cpu_times", return_value=times):
        fields = monitor._cpu_fields()

    assert set(fields) == set(CPU_STAT_FIELDS)
    assert fields["user"] == 100.0
    assert fields["iowait"] == 0.0
    assert fields["steal"] == 0.0


def test_proc_memory_falls_back_to_uss_without_pss(tmp_path: Path) -> None:
    """Linux reports PSS; platforms without it fall back to USS, not to zero."""
    monitor = _make_monitor(_make_bench(tmp_path))
    process = SimpleNamespace(memory_full_info=lambda: SimpleNamespace(uss=4 * 1024 * 1024))
    with patch("psutil.Process", return_value=process):
        assert monitor._proc_memory_bytes(1234) == 4 * 1024 * 1024

    process = SimpleNamespace(
        memory_full_info=lambda: SimpleNamespace(pss=2 * 1024 * 1024, uss=4 * 1024 * 1024)
    )
    with patch("psutil.Process", return_value=process):
        assert monitor._proc_memory_bytes(1234) == 2 * 1024 * 1024


def test_io_bytes_is_zero_where_the_platform_has_no_counters(tmp_path: Path) -> None:
    monitor = _make_monitor(_make_bench(tmp_path))
    with patch("psutil.Process", return_value=SimpleNamespace()):
        assert monitor._io_bytes(1234) == (0, 0)

    process = SimpleNamespace(
        io_counters=lambda: SimpleNamespace(read_bytes=500, write_bytes=250)
    )
    with patch("psutil.Process", return_value=process):
        assert monitor._io_bytes(1234) == (500, 250)


def test_collect_application_metrics_marks_a_vanished_process_missing(tmp_path: Path) -> None:
    """A pid that exits between resolution and reading must not kill the tick."""
    monitor = _make_monitor(_make_bench(tmp_path / "my-bench"))
    monitor._targets = {"web": 4242}
    app_log = tmp_path / "app.log"

    with (
        patch.object(type(monitor), "log_path", new_callable=PropertyMock, return_value=app_log),
        patch("psutil.Process", side_effect=psutil.NoSuchProcess(4242)),
    ):
        monitor.collect_application_metrics()

    entry = json.loads(app_log.read_text().splitlines()[-1])
    assert entry["processes"] == [{"service": "web", "pid": 4242, "missing": True}]


def _alerting_monitor(tmp_path: Path, **limits: int) -> Monitor:
    bench = _make_bench(tmp_path / "my-bench")
    for name, value in limits.items():
        setattr(bench.config.resource_limits, name, value)
    monitor = _make_monitor(bench)
    # MonitorConfigurator.setup() makes this directory on a real host, and it
    # is patched out here.
    monitor.alerts_path.parent.mkdir(parents=True, exist_ok=True)
    bench.config.resource_limits.webhook_endpoints = {"https://alerts.example.com": "tok"}
    return monitor


@contextmanager
def _captured_alerts():
    """Collect what reaches the webhook sink. Central is not enrolled on a bare
    test config, so notify() falls through to the webhooks alone."""
    payloads: list[dict] = []
    with patch(
        "pilot.core.alerts.send_alert",
        lambda endpoint, token, payload: payloads.append(payload),
    ):
        yield payloads


def _breached(payloads: list[dict]) -> list[list[str]]:
    return [[item["limit"] for item in payload["context"]["breached_limits"]] for payload in payloads]


def _system_record(cpu: float = 10.0, memory: float = 10.0, disk: float = 10.0) -> dict:
    return {
        "time": "2026-08-10T12:00:00+00:00",
        "cpu_percent": cpu,
        "memory": {"percent": memory},
        "storage": {"disk": {"percent": disk}},
    }


def test_alert_waits_for_the_breach_to_be_sustained(tmp_path: Path) -> None:
    monitor = _alerting_monitor(tmp_path, cpu_usage_limit=80)

    with _captured_alerts() as payloads:
        monitor.send_alert_if_required(_system_record(cpu=95.0))

    assert payloads == []
    assert list(json.loads(monitor.alerts_path.read_text())) == ["cpu_usage_limit"]


def test_alert_fires_once_the_breach_outlives_the_window(tmp_path: Path) -> None:
    monitor = _alerting_monitor(tmp_path, cpu_usage_limit=80)
    monitor.send_alert_if_required(_system_record(cpu=95.0))
    _age_alerts(monitor)

    with _captured_alerts() as payloads:
        monitor.send_alert_if_required(_system_record(cpu=95.0))
        monitor.send_alert_if_required(_system_record(cpu=95.0))

    assert _breached(payloads) == [["cpu_usage_limit"]], (
        "a sustained breach alerts once, not every tick"
    )


def test_alert_covers_only_the_limits_the_operator_set(tmp_path: Path) -> None:
    """Memory is over 90% here but has no configured limit, so it is not an alert."""
    monitor = _alerting_monitor(tmp_path, cpu_usage_limit=80)
    monitor.send_alert_if_required(_system_record(cpu=95.0, memory=99.0))
    _age_alerts(monitor)

    with _captured_alerts() as payloads:
        monitor.send_alert_if_required(_system_record(cpu=95.0, memory=99.0))

    assert _breached(payloads) == [["cpu_usage_limit"]]


def test_recovering_below_the_threshold_clears_the_alerts_file(tmp_path: Path) -> None:
    monitor = _alerting_monitor(tmp_path, cpu_usage_limit=80, disk_space_limit=90)

    with _captured_alerts():
        monitor.send_alert_if_required(_system_record(cpu=95.0, disk=95.0))
        monitor.send_alert_if_required(_system_record(cpu=95.0, disk=10.0))

        assert list(json.loads(monitor.alerts_path.read_text())) == ["cpu_usage_limit"]

        monitor.send_alert_if_required(_system_record(cpu=10.0, disk=10.0))

    assert not monitor.alerts_path.exists(), "nothing breaching means no file to keep"


def test_a_recovered_limit_starts_its_window_over(tmp_path: Path) -> None:
    monitor = _alerting_monitor(tmp_path, cpu_usage_limit=80)
    monitor.send_alert_if_required(_system_record(cpu=95.0))
    _age_alerts(monitor)
    monitor.send_alert_if_required(_system_record(cpu=10.0))

    with _captured_alerts() as payloads:
        monitor.send_alert_if_required(_system_record(cpu=95.0))

    assert payloads == [], "the second breach has to sustain on its own before alerting"


def test_a_corrupt_alerts_file_does_not_stop_the_tick(tmp_path: Path) -> None:
    monitor = _alerting_monitor(tmp_path, cpu_usage_limit=80)
    monitor.alerts_path.write_text("not json")

    with _captured_alerts():
        monitor.send_alert_if_required(_system_record(cpu=95.0))

    assert list(json.loads(monitor.alerts_path.read_text())) == ["cpu_usage_limit"]


def _age_alerts(monitor: Monitor) -> None:
    """Push every recorded breach back past the sustain window."""
    state = json.loads(monitor.alerts_path.read_text())
    for entry in state.values():
        entry["since"] -= ALERT_SUSTAINED_SECONDS + 1
    monitor.alerts_path.write_text(json.dumps(state))


def test_alert_body_matches_centrals_event_schema(tmp_path: Path) -> None:
    """Every sink gets the same event/message/context body. CentralClient maps
    ``event`` to Central's ``event_type`` on the wire."""
    bench = _make_bench(tmp_path / "my-bench")
    bench.config.resource_limits.cpu_usage_limit = 80
    monitor = _make_monitor(bench)

    payload = monitor._alert_payload(["cpu_usage_limit"], _system_record(cpu=95.0))

    assert set(payload) == {"event", "message", "context"}
    assert payload["event"] == "resource_limit_breached"
    assert payload["message"] == "my-bench: cpu_usage_limit at 95.0%"
    assert payload["context"]["breached_limits"] == [
        {"limit": "cpu_usage_limit", "threshold": 80, "reading": 95.0}
    ]
    assert payload["context"]["bench"] == "my-bench"
    json.dumps(payload)


def test_send_alert_posts_json_with_a_bearer_token() -> None:
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["body"] = json.loads(request.data.decode())
        captured["auth"] = request.get_header("Authorization")
        captured["type"] = request.get_header("Content-type")
        captured["timeout"] = timeout
        return nullcontext()

    with patch("urllib.request.urlopen", fake_urlopen):
        send_alert("https://alerts.example.com/pilot", "tok-123", {"event": "x", "context": {}})

    assert captured["url"] == "https://alerts.example.com/pilot"
    assert captured["method"] == "POST", "urllib sends the verb verbatim, so it has to be upper case"
    assert captured["body"] == {"event": "x", "context": {}}
    assert captured["auth"] == "Bearer tok-123"
    assert captured["type"] == "application/json"
    assert captured["timeout"] == ALERT_TIMEOUT_SECONDS


def test_alert_reaches_every_webhook_even_when_one_is_down(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path / "my-bench")
    bench.config.resource_limits.cpu_usage_limit = 80
    bench.config.resource_limits.webhook_endpoints = {
        "https://down.example.com": "tok-a",
        "https://up.example.com": "tok-b",
    }
    reached = []

    def fake_send(endpoint, token, payload):
        if endpoint == "https://down.example.com":
            raise urllib.error.URLError("connection refused")
        reached.append(endpoint)

    with patch("pilot.core.alerts.send_alert", fake_send):
        delivered = notify(bench, {"event": "x", "message": "m", "context": {}})

    assert reached == ["https://up.example.com"]
    assert delivered is True, "one sink answering is a delivered alert"


def test_an_alert_no_sink_accepted_is_not_delivered(tmp_path: Path) -> None:
    """The caller marks the condition notified off this, so a round where
    everything was down must not count."""
    bench = _make_bench(tmp_path / "my-bench")
    bench.config.resource_limits.webhook_endpoints = {"https://down.example.com": "tok"}

    def refuse(endpoint, token, payload):
        raise urllib.error.URLError("connection refused")

    with patch("pilot.core.alerts.send_alert", refuse):
        delivered = notify(bench, {"event": "x", "message": "m", "context": {}})

    assert delivered is False


def test_an_undelivered_alert_is_recorded_locally_once(tmp_path: Path) -> None:
    """A due condition stays due until a sink accepts it, so the delivery attempt
    repeats every tick. The bench's own feed must not grow a row per tick with it."""
    monitor = _alerting_monitor(tmp_path, cpu_usage_limit=80)

    def refuse(endpoint, token, payload):
        raise urllib.error.URLError("connection refused")

    with patch("pilot.core.alerts.send_alert", refuse):
        monitor.send_alert_if_required(_system_record(cpu=95.0))
        _age_alerts(monitor)
        for _ in range(5):
            monitor.send_alert_if_required(_system_record(cpu=95.0))

    recorded = monitor.bench.notifications.list(10)
    assert [item.title for item in recorded] == ["Resource limit breached"]


def test_a_breach_that_recovers_and_returns_is_recorded_again(tmp_path: Path) -> None:
    """`recorded` lives with the condition, so a cleared limit that breaks a
    second time is a new incident and earns a new row."""
    monitor = _alerting_monitor(tmp_path, cpu_usage_limit=80)

    with _captured_alerts():
        monitor.send_alert_if_required(_system_record(cpu=95.0))
        _age_alerts(monitor)
        monitor.send_alert_if_required(_system_record(cpu=95.0))

        monitor.send_alert_if_required(_system_record(cpu=10.0))

        monitor.send_alert_if_required(_system_record(cpu=95.0))
        _age_alerts(monitor)
        monitor.send_alert_if_required(_system_record(cpu=95.0))

    assert len(monitor.bench.notifications.list(10)) == 2


def test_a_local_record_that_failed_is_retried_on_the_next_tick(tmp_path: Path) -> None:
    """Recording once per breach must not mean losing the breach when the write
    itself fails - the condition stays unrecorded so the next tick tries again."""
    from pilot.core.notification import NotificationStore

    monitor = _alerting_monitor(tmp_path, cpu_usage_limit=80)

    def refuse(endpoint, token, payload):
        raise urllib.error.URLError("connection refused")

    with patch("pilot.core.alerts.send_alert", refuse):
        monitor.send_alert_if_required(_system_record(cpu=95.0))
        _age_alerts(monitor)

        with patch.object(NotificationStore, "create", side_effect=OSError("no space left on device")):
            monitor.send_alert_if_required(_system_record(cpu=95.0))

        assert monitor.bench.notifications.list(10) == [], "the failed write left nothing behind"

        monitor.send_alert_if_required(_system_record(cpu=95.0))

    assert [item.title for item in monitor.bench.notifications.list(10)] == ["Resource limit breached"]


def test_a_delivered_alert_whose_local_write_failed_is_still_recorded(tmp_path: Path) -> None:
    """Delivery retires the condition from `due` for good. The bench's own record
    is tracked apart from that, so a failed write is still retried afterwards."""
    from pilot.core.notification import NotificationStore

    monitor = _alerting_monitor(tmp_path, cpu_usage_limit=80)

    with _captured_alerts():
        monitor.send_alert_if_required(_system_record(cpu=95.0))
        _age_alerts(monitor)

        with patch.object(NotificationStore, "create", side_effect=OSError("no space left on device")):
            monitor.send_alert_if_required(_system_record(cpu=95.0))

        assert monitor.bench.notifications.list(10) == []
        assert json.loads(monitor.alerts_path.read_text())["cpu_usage_limit"]["notified"] is True

        monitor.send_alert_if_required(_system_record(cpu=95.0))

    assert [item.title for item in monitor.bench.notifications.list(10)] == ["Resource limit breached"]
