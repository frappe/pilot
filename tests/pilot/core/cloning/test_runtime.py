import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from threading import Event

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


@pytest.mark.parametrize("restart", [False, True])
def test_temporary_redis_owner_waits_for_other_clone(source, monkeypatch, restart):
    if not runtime.redis_server_binary():
        pytest.skip("Redis server unavailable")
    with socket.socket() as cache, socket.socket() as queue:
        cache.bind(("127.0.0.1", 0))
        queue.bind(("127.0.0.1", 0))
        source.config.redis.cache_port = cache.getsockname()[1]
        source.config.redis.queue_port = queue.getsockname()[1]
    owner_ready = Event()
    borrower_ready = Event()
    finish_owner = Event()
    finish_borrower = Event()
    shutdown_waiting = Event()
    flock = runtime.fcntl.flock
    processes = []
    runtimes = []
    popen = runtime.subprocess.Popen

    def observe_lock(descriptor, operation):
        if isinstance(descriptor, int) and operation == runtime.fcntl.LOCK_EX:
            shutdown_waiting.set()
        return flock(descriptor, operation)

    def spawn(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(runtime.fcntl, "flock", observe_lock)
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)

    def clone(ready, finish):
        services = runtime.CloneRuntime(source, allow_existing=True)
        runtimes.append(services)
        with services:
            ready.set()
            assert finish.wait(10)

    ports = (source.config.redis.cache_port, source.config.redis.queue_port)
    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(clone, owner_ready, finish_owner)
        borrower = None
        try:
            if not owner_ready.wait(5):
                owner.result(timeout=1)
                pytest.fail("Owner runtime did not become ready")
            if restart:
                for process in processes:
                    runtime.CloneRuntime.stop(process)
            borrower = executor.submit(clone, borrower_ready, finish_borrower)
            assert borrower_ready.wait(5)
            finish_owner.set()
            assert shutdown_waiting.wait(5)
            assert not owner.done()
            assert all(runtime.CloneRuntime.has_existing_redis(port) for port in ports)
            assert len(processes) == (4 if restart else 2)
        finally:
            finish_owner.set()
            finish_borrower.set()
            try:
                owner.result(timeout=5)
                if borrower is not None:
                    borrower.result(timeout=5)
            finally:
                for services in runtimes:
                    with suppress(OSError, ValueError):
                        if services.lease is not None and not services.lease.closed:
                            runtime.fcntl.flock(services.lease.fileno(), runtime.fcntl.LOCK_UN)
    assert all(process.poll() is not None for process in processes)
    assert not any(runtime.CloneRuntime.has_existing_redis(port) for port in ports)
