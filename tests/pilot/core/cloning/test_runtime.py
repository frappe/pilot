import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from threading import Event
from types import SimpleNamespace

import pytest

from pilot.core.site import clone_runtime as runtime


def test_site_clone_reuses_running_redis_without_stopping_it(source, monkeypatch):
    monkeypatch.setattr(runtime, "redis_server_binary", lambda: "/redis")
    monkeypatch.setattr(runtime.CloneRuntime, "has_existing_redis", staticmethod(lambda port: True))

    def unexpected(*args, **kwargs):
        raise AssertionError("Running Redis must not be replaced")

    monkeypatch.setattr(runtime.RedisManager, "generate_configs", unexpected)
    monkeypatch.setattr(runtime.subprocess, "Popen", unexpected)
    with runtime.CloneRuntime(source, allow_existing=True):
        pass


@pytest.fixture
def redis_clones(source, monkeypatch):
    if not runtime.redis_server_binary():
        pytest.skip("Redis server unavailable")
    with socket.socket() as cache, socket.socket() as queue:
        cache.bind(("127.0.0.1", 0))
        queue.bind(("127.0.0.1", 0))
        source.config.redis.cache_port = cache.getsockname()[1]
        source.config.redis.queue_port = queue.getsockname()[1]
    state = SimpleNamespace(
        owner_ready=Event(),
        borrower_ready=Event(),
        finish_owner=Event(),
        finish_borrower=Event(),
        shutdown_waiting=Event(),
        processes=[],
        runtimes=[],
        futures=[],
        ports=(source.config.redis.cache_port, source.config.redis.queue_port),
    )
    flock, popen = runtime.fcntl.flock, runtime.subprocess.Popen

    def observe_lock(descriptor, operation):
        if isinstance(descriptor, int) and operation == runtime.fcntl.LOCK_EX:
            state.shutdown_waiting.set()
        return flock(descriptor, operation)

    def spawn(*args, **kwargs):
        process = popen(*args, **kwargs)
        state.processes.append(process)
        return process

    def clone(ready, finish):
        services = runtime.CloneRuntime(source, allow_existing=True)
        state.runtimes.append(services)
        with services:
            ready.set()
            assert finish.wait(10)

    monkeypatch.setattr(runtime.fcntl, "flock", observe_lock)
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    with ThreadPoolExecutor(max_workers=2) as executor:

        def start(ready, finish):
            future = executor.submit(clone, ready, finish)
            state.futures.append(future)
            return future

        state.start = start
        try:
            yield state
        finally:
            finish_clones(state)
    assert all(process.poll() is not None for process in state.processes)
    assert not any(runtime.CloneRuntime.has_existing_redis(port) for port in state.ports)


def finish_clones(state):
    state.finish_owner.set()
    state.finish_borrower.set()
    try:
        for future in state.futures:
            future.result(timeout=5)
    finally:
        for services in state.runtimes:
            with suppress(OSError, ValueError):
                if services.lease is not None and not services.lease.closed:
                    runtime.fcntl.flock(services.lease.fileno(), runtime.fcntl.LOCK_UN)


@pytest.mark.parametrize("restart", [False, True])
def test_temporary_redis_owner_waits_for_other_clone(redis_clones, restart):
    state = redis_clones
    owner = state.start(state.owner_ready, state.finish_owner)
    if not state.owner_ready.wait(5):
        owner.result(timeout=1)
        pytest.fail("Owner runtime did not become ready")
    if restart:
        for process in state.processes:
            runtime.CloneRuntime.stop(process)
    state.start(state.borrower_ready, state.finish_borrower)
    assert state.borrower_ready.wait(5)
    state.finish_owner.set()
    assert state.shutdown_waiting.wait(5)
    assert not owner.done()
    assert all(runtime.CloneRuntime.has_existing_redis(port) for port in state.ports)
    assert len(state.processes) == (4 if restart else 2)
