"""Rate/delta math for DatabaseMonitoringProvider (independent of BenchConfig)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from admin.backend.providers.database_monitoring import DatabaseMonitoringProvider


def _provider() -> DatabaseMonitoringProvider:
    return DatabaseMonitoringProvider(bench_root=None, window="1h")  # type: ignore[arg-type]


def _sample(**counters: int) -> dict:
    base = {
        "Com_insert": 0,
        "Com_update": 0,
        "Com_delete": 0,
        "Com_select": 0,
        "Questions": 0,
        "Innodb_buffer_pool_reads": 0,
        "Innodb_buffer_pool_read_requests": 0,
        "Innodb_row_lock_time": 0,
        "Innodb_row_lock_waits": 0,
        "Threads_connected": 0,
        "max_connections": 151,
    }
    base.update(counters)
    return base


def test_query_counts_and_other_clamping() -> None:
    t0 = datetime.now(UTC)
    t1 = t0 + timedelta(seconds=10)
    before = _sample(Com_insert=0, Com_select=0, Questions=0)
    # Deltas are raw counts over the interval, not per-second rates.
    after = _sample(Com_insert=100, Com_select=200, Questions=250)

    point = _provider()._point((t0, before), (t1, after))

    assert point["Insert"] == 100
    assert point["Select"] == 200
    # Other = 250 - (100+200) = -50, clamped to 0.
    assert point["Other"] == 0


def test_buffer_pool_and_lock_wait() -> None:
    t0 = datetime.now(UTC)
    t1 = t0 + timedelta(seconds=5)
    before = _sample()
    after = _sample(
        Innodb_buffer_pool_reads=50,
        Innodb_buffer_pool_read_requests=1000,
        Innodb_row_lock_time=200,
        Innodb_row_lock_waits=4,
        Threads_connected=7,
        innodb_buffer_pool_size=134217728,
        total_ram_mb=1024,
    )

    point = _provider()._point((t0, before), (t1, after))

    assert point["Buffer Pool Miss %"] == 5.0  # 50 / 1000 * 100
    assert point["Avg Row Lock Wait"] == 50.0  # 200 / 4
    assert point["Connected"] == 7
    assert point["Max Connections"] == 151
    assert point["Buffer Pool Size"] == 134217728
    assert point["Buffer Pool % RAM"] == 12.5  # 128 MB of 1024 MB


def test_zero_denominators_are_safe() -> None:
    t0 = datetime.now(UTC)
    t1 = t0 + timedelta(seconds=10)
    point = _provider()._point((t0, _sample()), (t1, _sample()))

    assert point["Buffer Pool Miss %"] == 0.0
    assert point["Avg Row Lock Wait"] == 0.0
