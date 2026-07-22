import json
from pathlib import Path
from unittest.mock import patch

from pilot.config import BenchConfig, MariaDBConfig, ProductionConfig, RedisConfig, WorkerConfig
from pilot.core.bench import Bench
from pilot.core.server.monitoring import Monitor, MonitorConfigurator


def _make_bench(path: Path, name: str = "my-bench") -> Bench:
    config = BenchConfig(
        name=name,
        python_version="3.14",
        mariadb=MariaDBConfig(),
        redis=RedisConfig(),
        workers=WorkerConfig(),
    )
    return Bench(config, path)


def _make_monitor(bench: Bench, authority_file: Path | None = None) -> Monitor:
    with patch.object(MonitorConfigurator, "setup"):
        monitor = Monitor(bench)
    # Keep the authority sentinel inside tmp_path - never touch /var/log in tests.
    monitor.bench.config.monitor.authority_file_path = authority_file or (
        bench.path.parent / ".bench-authority"
    )
    monitor.bench.config.monitor.log_path = bench.path / f"{bench.config.name}-stats.log"
    return monitor


def _sibling(name: str, process_manager: str = "") -> tuple[Path, BenchConfig]:
    config = BenchConfig(
        name=name,
        python_version="3.14",
        mariadb=MariaDBConfig(),
        redis=RedisConfig(),
        workers=WorkerConfig(),
        production=ProductionConfig(enabled=bool(process_manager), process_manager=process_manager),
    )
    return Path(f"/fake/{name}"), config


def test_authority_claimed_when_no_file_exists(tmp_path: Path) -> None:
    authority_file = tmp_path / ".bench-authority"
    monitor = _make_monitor(_make_bench(tmp_path / "my-bench"), authority_file)

    assert monitor.is_system_log_authority() is True
    assert authority_file.read_text() == "my-bench"


def test_authority_true_when_already_recorded(tmp_path: Path) -> None:
    authority_file = tmp_path / ".bench-authority"
    authority_file.write_text("my-bench")
    monitor = _make_monitor(_make_bench(tmp_path / "my-bench"), authority_file)

    assert monitor.is_system_log_authority() is True


def test_authority_false_when_sibling_runs_systemd(tmp_path: Path) -> None:
    authority_file = tmp_path / ".bench-authority"
    authority_file.write_text("other-bench")
    monitor = _make_monitor(_make_bench(tmp_path / "my-bench"), authority_file)

    with patch(
        "pilot.core.server.monitoring_config.iter_sibling_benches",
        return_value=iter([_sibling("other-bench", "systemd")]),
    ):
        assert monitor.is_system_log_authority() is False


def test_authority_false_when_sibling_runs_supervisor(tmp_path: Path) -> None:
    authority_file = tmp_path / ".bench-authority"
    authority_file.write_text("other-bench")
    monitor = _make_monitor(_make_bench(tmp_path / "my-bench"), authority_file)

    with patch(
        "pilot.core.server.monitoring_config.iter_sibling_benches",
        return_value=iter([_sibling("other-bench", "supervisor")]),
    ):
        assert monitor.is_system_log_authority() is False


def test_authority_stolen_when_recorded_bench_is_in_dev_mode(tmp_path: Path) -> None:
    authority_file = tmp_path / ".bench-authority"
    authority_file.write_text("other-bench")
    monitor = _make_monitor(_make_bench(tmp_path / "my-bench"), authority_file)

    with patch(
        "pilot.core.server.monitoring_config.iter_sibling_benches",
        return_value=iter([_sibling("other-bench", "")]),
    ):
        assert monitor.is_system_log_authority() is True
    assert authority_file.read_text() == "my-bench"


def test_authority_stolen_when_recorded_bench_no_longer_exists(tmp_path: Path) -> None:
    authority_file = tmp_path / ".bench-authority"
    authority_file.write_text("dropped-bench")
    monitor = _make_monitor(_make_bench(tmp_path / "my-bench"), authority_file)

    with patch("pilot.core.server.monitoring_config.iter_sibling_benches", return_value=iter([])):
        assert monitor.is_system_log_authority() is True
    assert authority_file.read_text() == "my-bench"


def test_exactly_one_bench_holds_authority(tmp_path: Path) -> None:
    """When two benches run, exactly one is the system-log authority."""
    authority_file = tmp_path / ".bench-authority"

    monitor_a = _make_monitor(_make_bench(tmp_path / "bench-a", "bench-a"), authority_file)
    monitor_b = _make_monitor(_make_bench(tmp_path / "bench-b", "bench-b"), authority_file)

    # bench-a claims authority (file absent)
    assert monitor_a.is_system_log_authority() is True

    # bench-b sees bench-a as the running authority
    with patch(
        "pilot.core.server.monitoring_config.iter_sibling_benches",
        return_value=iter([_sibling("bench-a", "systemd")]),
    ):
        assert monitor_b.is_system_log_authority() is False


def _fake_proc_reads(monitor: Monitor) -> None:
    """Stub out /proc reads so tests don't depend on the host machine state."""
    monitor._load_average = lambda: (0.5, 0.4, 0.3)  # type: ignore[method-assign]
    monitor._system_cpu = 12.5
    monitor._memory_usage = lambda: {
        "total_mb": 8192.0,
        "used_mb": 4096.0,
        "available_mb": 4096.0,
        "percent": 50.0,
    }  # type: ignore[method-assign]
    monitor._storage_usage = lambda: {
        "disk": {"total_mb": 51200.0, "used_mb": 20480.0, "free_mb": 30720.0, "percent": 40.0}
    }  # type: ignore[method-assign]


def test_collect_system_metrics_writes_to_system_log_file(tmp_path: Path) -> None:
    system_log_file = tmp_path / "bench-system-stats.log"
    monitor = _make_monitor(_make_bench(tmp_path / "my-bench"))
    monitor.bench.config.monitor.system_log_path = system_log_file
    _fake_proc_reads(monitor)

    monitor.collect_system_metrics()

    assert system_log_file.exists()
    entry = json.loads(system_log_file.read_text().splitlines()[-1])
    assert entry["load_avg"] == [0.5, 0.4, 0.3]
    assert entry["cpu_percent"] == 12.5
    assert entry["memory"]["percent"] == 50.0


def test_collect_system_metrics_does_not_write_app_log(tmp_path: Path) -> None:
    """System metrics must never bleed into the per-bench application log."""
    system_log_file = tmp_path / "bench-system-stats.log"
    monitor = _make_monitor(_make_bench(tmp_path / "my-bench"))
    monitor.bench.config.monitor.system_log_path = system_log_file
    _fake_proc_reads(monitor)

    monitor.collect_system_metrics()

    assert not monitor.log_path.exists()


def test_collect_system_metrics_skipped_when_not_authority(tmp_path: Path) -> None:
    authority_file = tmp_path / ".bench-authority"
    authority_file.write_text("other-bench")
    system_log_file = tmp_path / "bench-system-stats.log"
    monitor = _make_monitor(_make_bench(tmp_path / "my-bench"), authority_file)
    monitor.bench.config.monitor.system_log_path = system_log_file

    siblings = [_sibling("other-bench", "systemd")]
    with patch("pilot.core.server.monitoring_config.iter_sibling_benches", return_value=iter(siblings)):
        monitor.collect_system_metrics()

    assert not system_log_file.exists()


def test_collect_system_metrics_includes_storage(tmp_path: Path) -> None:
    system_log_file = tmp_path / "bench-system-stats.log"
    monitor = _make_monitor(_make_bench(tmp_path / "my-bench"))
    monitor.bench.config.monitor.system_log_path = system_log_file
    _fake_proc_reads(monitor)

    monitor.collect_system_metrics()

    entry = json.loads(system_log_file.read_text().splitlines()[-1])
    assert "storage" in entry
    assert "disk" in entry["storage"]
    assert entry["storage"]["disk"]["percent"] == 40.0


def test_disk_usage_returns_expected_fields(tmp_path: Path) -> None:
    monitor = _make_monitor(_make_bench(tmp_path))
    result = monitor._disk_usage(tmp_path)
    assert result["total_mb"] > 0
    assert result["used_mb"] >= 0
    assert result["free_mb"] >= 0
    assert 0.0 <= result["percent"] <= 100.0
    assert abs(result["total_mb"] - result["used_mb"] - result["free_mb"]) < 1.0


def test_storage_usage_always_includes_disk(tmp_path: Path) -> None:
    monitor = _make_monitor(_make_bench(tmp_path))
    result = monitor._storage_usage()
    assert "disk" in result
    assert result["disk"]["total_mb"] > 0


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

    assert set(result) >= {"total_mb", "used_mb", "cached_mb", "free_mb", "swap_used_mb", "percent"}
    assert abs(result["total_mb"] - result["used_mb"] - result["cached_mb"] - result["free_mb"]) < 1.0


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


def test_disk_io_fields_ignores_partitions(tmp_path: Path) -> None:
    monitor = _make_monitor(_make_bench(tmp_path))
    diskstats = tmp_path / "diskstats"
    diskstats.write_text(
        "   8       0 sda 100 0 2000 0 50 0 1000 0 0 0 0\n"
        "   8       1 sda1 40 0 800 0 20 0 400 0 0 0 0\n"
        "  259       0 nvme0n1 10 0 200 0 5 0 100 0 0 0 0\n"
    )
    with patch(
        "pilot.core.server.monitoring_proc.Path",
        side_effect=lambda p: diskstats if p == "/proc/diskstats" else Path(p),
    ):
        result = monitor._disk_io_fields()

    assert result == {"read_bytes": (2000 + 200) * 512, "write_bytes": (1000 + 100) * 512}
