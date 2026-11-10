"""Wait until a database accepts connections on `HOST:PORT`.

Stdlib only and given the address, so a sandboxed unit can run it without reading bench.toml."""

from __future__ import annotations

import socket
import sys
import time

TIMEOUT_SECONDS = 60


def parse_address(address: str) -> tuple[str, int]:
    host, _, port = address.rpartition(":")
    if not host or not port.isdigit() or "/" in host:
        raise ValueError(f"Expected HOST:PORT, got {address!r}.")
    return host, int(port)


def is_accepting(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=5).close()
    except OSError:
        return False
    return True


def main(address: str, timeout_seconds: float = TIMEOUT_SECONDS) -> int:
    try:
        host, port = parse_address(address)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    deadline = time.monotonic() + timeout_seconds
    while not is_accepting(host, port):
        if time.monotonic() >= deadline:
            print("The database did not become ready. Check its service and restart the workload.", file=sys.stderr)
            return 1
        time.sleep(0.1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
