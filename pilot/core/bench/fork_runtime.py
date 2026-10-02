from __future__ import annotations

import socket
import subprocess
import time
from contextlib import ExitStack

from pilot.exceptions import BenchError
from pilot.managers.redis import RedisManager, redis_server_binary


class ForkRuntime:
    """Own the temporary Redis processes required during restore and asset builds."""

    def __init__(self, bench, *, allow_existing=False) -> None:
        self.bench = bench
        self.allow_existing = allow_existing
        self.stack = ExitStack()

    def __enter__(self):
        binary = redis_server_binary()
        if not binary:
            raise BenchError("Redis is required to fork a development bench.")
        ports = (self.bench.config.redis.cache_port, self.bench.config.redis.queue_port)
        if not self.allow_existing or not all(self.has_existing_redis(port) for port in ports):
            RedisManager(self.bench.config.redis, self.bench).generate_configs()
        try:
            for name, port in (
                ("redis_cache", self.bench.config.redis.cache_port),
                ("redis_queue", self.bench.config.redis.queue_port),
            ):
                self.start(binary, name, port)
        except BaseException:
            self.stack.close()
            raise
        return self

    def start(self, binary: str, name: str, port: int) -> None:
        if self.allow_existing and self.has_existing_redis(port):
            return
        log = self.stack.enter_context((self.bench.logs_path / f"fork-{name}.log").open("a"))
        process = subprocess.Popen(
            [binary, str(self.bench.config_path / f"{name}.conf")],
            stdout=log,
            stderr=log,
            cwd=self.bench.path,
        )
        self.stack.callback(self.stop, process)
        for _ in range(100):
            if process.poll() is not None:
                raise BenchError(f"Fork Redis {name} exited; inspect {log.name}.")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1) as connection:
                    connection.sendall(b"*2\r\n$4\r\nINFO\r\n$6\r\nserver\r\n")
                    if f"process_id:{process.pid}\r\n".encode() in connection.recv(8192):
                        return
            except OSError:
                pass
            time.sleep(0.05)
        raise BenchError(f"Fork Redis {name} did not become ready.")

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
        self.stack.close()
