"""Tests for the memory budget one asset build is allowed."""

from __future__ import annotations

import pytest

from pilot.core.build_memory import calculate_build_memory


def test_share_bounds_the_build_on_an_idle_host():
    sizing = calculate_build_memory(total_memory_mb=4096, available_memory_mb=4000)
    assert sizing.limit_mb == 2457
    assert sizing.can_build


def test_available_memory_bounds_the_build_on_a_loaded_host():
    sizing = calculate_build_memory(total_memory_mb=8192, available_memory_mb=2000)
    assert sizing.limit_mb == 1950
    assert sizing.can_build


def test_a_measured_full_build_fits_on_a_two_gigabyte_host():
    """A full build peaks around 1.1GB, so 2GB hosts must still clear it."""
    sizing = calculate_build_memory(total_memory_mb=2048, available_memory_mb=2048)
    assert sizing.limit_mb >= 1140
    assert sizing.can_build


def test_build_is_refused_when_too_little_memory_is_free():
    sizing = calculate_build_memory(total_memory_mb=4096, available_memory_mb=200)
    assert not sizing.can_build
    assert "Not enough free memory" in sizing.refusal_reason


def test_small_host_cannot_run_a_build():
    sizing = calculate_build_memory(total_memory_mb=512, available_memory_mb=512)
    assert not sizing.can_build


def test_limit_never_goes_negative():
    sizing = calculate_build_memory(total_memory_mb=4096, available_memory_mb=10)
    assert sizing.limit_mb == 0
    assert not sizing.can_build


def test_total_memory_must_be_positive():
    with pytest.raises(ValueError):
        calculate_build_memory(total_memory_mb=0, available_memory_mb=1000)
