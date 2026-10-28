from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.exceptions import BenchError
from pilot.managers.redis import RedisManager
from tests.pilot.managers.test_managers_extra import make_bench


@pytest.fixture
def manager(tmp_path: Path) -> RedisManager:
    bench = make_bench(tmp_path)
    return RedisManager(bench.config.redis, bench)


@contextmanager
def fake_servers(
    manager: RedisManager,
    listening: set[int],
    fail_for: str = "",
    starts: bool = True,
    binary: str | None = "redis-server",
) -> Iterator[list[MagicMock]]:
    """Patch Popen and the port check. A started server listens on its port unless `starts` is
    False, and starting the server for the `fail_for` config file raises OSError."""
    started: list[MagicMock] = []

    def popen(argv: list[str], **_) -> MagicMock:
        name = Path(argv[-1]).name
        if name == fail_for:
            raise OSError(f"cannot start {name}")
        if starts:
            listening.add(manager.servers[name])
        started.append(MagicMock(name=name))
        return started[-1]

    with (
        patch("pilot.managers.redis.subprocess.Popen", side_effect=popen),
        patch("pilot.managers.redis.is_port_listening", side_effect=lambda port: port in listening),
        patch("pilot.managers.redis.redis_server_binary", return_value=binary),
    ):
        yield started


def test_servers_already_listening_are_reused_and_left_running(manager: RedisManager) -> None:
    with fake_servers(manager, {13000, 11000}) as started, manager.running():
        pass

    assert started == []


def test_stopped_servers_are_started_for_the_duration_and_stopped_after(manager: RedisManager) -> None:
    with fake_servers(manager, {13000}) as started:
        with manager.running():
            [queue] = started
            assert queue.mock_calls == []
        queue.terminate.assert_called_once()
        queue.wait.assert_called_once()


def test_a_server_that_never_listens_fails_and_is_stopped(manager: RedisManager) -> None:
    with (
        fake_servers(manager, set(), starts=False) as started,
        patch("pilot.managers.redis.time.sleep"),
        patch("pilot.managers.redis.time.monotonic", side_effect=[0, 5, 11]),
        pytest.raises(BenchError, match="did not start on port 13000"),
        manager.running(),
    ):
        pass

    [cache] = started
    cache.terminate.assert_called_once()


def test_a_failure_starting_the_second_server_stops_the_first(manager: RedisManager) -> None:
    with (
        fake_servers(manager, set(), fail_for="redis_queue.conf") as started,
        pytest.raises(OSError, match=r"cannot start redis_queue\.conf"),
        manager.running(),
    ):
        pass

    [cache] = started
    cache.terminate.assert_called_once()
    cache.wait.assert_called_once()


def test_a_missing_redis_binary_fails_before_starting_any_server(manager: RedisManager) -> None:
    with (
        fake_servers(manager, set(), binary=None) as started,
        pytest.raises(BenchError, match="redis-server is not installed"),
        manager.running(),
    ):
        pass

    assert started == []


def test_a_missing_redis_binary_is_fine_when_every_server_is_already_up(manager: RedisManager) -> None:
    with fake_servers(manager, {13000, 11000}, binary=None) as started, manager.running():
        pass

    assert started == []
