"""Tests for the memory ceiling on the two redis instances."""

from __future__ import annotations

import pytest

from pilot.core.build_memory import calculate_redis_memory


def test_ceiling_scales_with_the_host():
    assert calculate_redis_memory(8192) == 163


def test_a_small_host_still_gets_a_workable_floor():
    assert calculate_redis_memory(512) == 32


def test_a_large_host_does_not_hand_redis_the_machine():
    assert calculate_redis_memory(131072) == 512


def test_total_memory_must_be_positive():
    with pytest.raises(ValueError):
        calculate_redis_memory(0)


def test_cache_evicts_but_the_queue_refuses_writes(tmp_path, monkeypatch):
    """Evicting a queued job loses work, so only the cache may drop keys."""
    from pilot.config import RedisConfig
    from pilot.managers import redis as redis_module

    class FakeBench:
        config_path = tmp_path

    manager = redis_module.RedisManager.__new__(redis_module.RedisManager)
    manager.config = RedisConfig(cache_port=13000, queue_port=11000)
    manager.bench = FakeBench()
    manager.generate_configs()

    cache = (tmp_path / "redis_cache.conf").read_text()
    queue = (tmp_path / "redis_queue.conf").read_text()
    assert "maxmemory-policy allkeys-lru" in cache
    assert "maxmemory-policy noeviction" in queue
    assert "maxmemory " in cache and "maxmemory " in queue
