from __future__ import annotations

import fcntl
import socket
import subprocess
import time
from contextlib import ExitStack

from pilot.exceptions import BenchError
from pilot.internal.atomic_file import exclusive_file_lock
from pilot.managers.redis import RedisManager, redis_server_binary
from pilot.utils import open_private


class CloneRuntime:
    """Own the temporary Redis processes required during restore and asset builds."""

    def __init__(self, bench, *, allow_existing=False) -> None:
        self.bench = bench
        self.allow_existing = allow_existing
        self.stack = ExitStack()
        self.owns_processes = False
        self.lease = None

    def __enter__(self):
        binary = redis_server_binary()
        if not binary:
            raise BenchError("Redis is required to clone a site.")
        with exclusive_file_lock(self.bench.path / "clone-runtime"):
            try:
                self.lease = self.stack.enter_context(
                    open_private(self.bench.path / ".clone-runtime-users", mode="a")
                )
                fcntl.flock(self.lease.fileno(), fcntl.LOCK_SH)
                self.start_services(binary)
            except BaseException:
                self.stack.close()
                raise
        return self

    def start_services(self, binary: str) -> None:
        ports = (self.bench.config.redis.cache_port, self.bench.config.redis.queue_port)
        if not self.allow_existing or not all(self.has_existing_redis(port) for port in ports):
            RedisManager(self.bench.config.redis, self.bench).generate_configs()
        for name, port in (
            ("redis_cache", self.bench.config.redis.cache_port),
            ("redis_queue", self.bench.config.redis.queue_port),
        ):
            self.start(binary, name, port)

    def start(self, binary: str, name: str, port: int) -> None:
        if self.allow_existing and self.has_existing_redis(port):
            return
        log = self.stack.enter_context((self.bench.logs_path / f"clone-{name}.log").open("a"))
        process = subprocess.Popen(
            [binary, str(self.bench.config_path / f"{name}.conf")],
            stdout=log,
            stderr=log,
            cwd=self.bench.path,
        )
        self.stack.callback(self.stop, process)
        self.owns_processes = True
        for _ in range(100):
            if process.poll() is not None:
                raise BenchError(f"Clone Redis {name} exited; inspect {log.name}.")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1) as connection:
                    connection.sendall(b"*2\r\n$4\r\nINFO\r\n$6\r\nserver\r\n")
                    if f"process_id:{process.pid}\r\n".encode() in connection.recv(8192):
                        return
            except OSError:
                pass
            time.sleep(0.05)
        raise BenchError(f"Clone Redis {name} did not become ready.")

    @staticmethod
    def has_existing_redis(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1) as connection:
                connection.sendall(b"*1\r\n$4\r\nPING\r\n")
                return connection.recv(64) == b"+PONG\r\n"
        except OSError:
            return False

    @staticmethod
    def stop(process) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def __exit__(self, *args) -> None:
        if not self.owns_processes:
            self.stack.close()
            return
        assert self.lease is not None
        fcntl.flock(self.lease.fileno(), fcntl.LOCK_UN)
        with exclusive_file_lock(self.bench.path / "clone-runtime"):
            fcntl.flock(self.lease.fileno(), fcntl.LOCK_EX)
            self.stack.close()
