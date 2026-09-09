"""Tests for the memory ceilings on long-running bench processes."""

from __future__ import annotations

import pytest

from pilot.core.process_memory import calculate_process_memory

_MEASURED_PEAKS = {"web": 141, "worker_pool": 118, "socketio": 60}


@pytest.mark.parametrize("name", sorted(_MEASURED_PEAKS))
@pytest.mark.parametrize("total_memory_mb", [1024, 2048, 4096, 8192])
def test_every_limit_clears_the_measured_peak(name, total_memory_mb):
    """A ceiling under the working set would restart-loop the service."""
    sizing = calculate_process_memory(name, total_memory_mb, database_memory_max_mb=512)
    assert sizing is not None
    assert sizing.memory_max_mb >= _MEASURED_PEAKS[name] * 2


@pytest.mark.parametrize("name", sorted(_MEASURED_PEAKS))
def test_throttling_starts_before_the_kill(name):
    sizing = calculate_process_memory(name, 4096, database_memory_max_mb=1182)
    assert sizing is not None
    assert sizing.memory_high_mb < sizing.memory_max_mb


@pytest.mark.parametrize("worker_count", [1, 2, 4, 8])
def test_the_worker_pool_ceiling_scales_with_its_workers(worker_count):
    """One unit runs every worker, so a fixed ceiling would kill healthy ones."""
    sizing = calculate_process_memory(
        "worker_pool", 8192, database_memory_max_mb=3172, worker_count=worker_count
    )
    assert sizing is not None
    assert sizing.memory_max_mb >= _MEASURED_PEAKS["worker_pool"] * worker_count * 2


def test_many_workers_still_leave_web_above_its_own_peak():
    """A bigger pool takes a bigger share, but never below web's working set."""
    sizing = calculate_process_memory("web", 4096, database_memory_max_mb=1182, worker_count=8)
    assert sizing is not None
    assert sizing.memory_max_mb >= _MEASURED_PEAKS["web"] * 2


def test_a_process_without_a_measured_profile_is_left_uncapped():
    assert calculate_process_memory("redis_cache", 4096, database_memory_max_mb=1182) is None


def test_total_memory_must_be_positive():
    with pytest.raises(ValueError):
        calculate_process_memory("web", 0, database_memory_max_mb=256)


def test_units_carry_the_limits_and_admin_does_not(tmp_path):
    from pilot.managers.processes.definitions import ProcessDefinition
    from pilot.managers.processes.systemd_render import SystemdRenderer

    renderer = SystemdRenderer("test-bench")
    capped = ProcessDefinition(
        name="web",
        argv=["/bin/true"],
        log_file=tmp_path / "web.log",
        memory_high_mb=296,
        memory_max_mb=423,
    )
    text = renderer.render(capped)
    assert "MemoryHigh=296M" in text
    assert "MemoryMax=423M" in text
    # Without this a leaking service spills into swap and grinds instead of dying.
    assert "MemorySwapMax=0" in text
    assert "OOMPolicy=stop" in text

    uncapped = ProcessDefinition(name="admin", argv=["/bin/true"], log_file=tmp_path / "a.log")
    assert "MemoryMax" not in renderer.render(uncapped)
