"""Wait until a database accepts connections: `wait_for_socket.py unix:PATH` or `tcp:HOST:PORT`.

Stdlib only and given the address, so a sandboxed unit can run it without reading bench.toml."""

from __future__ import annotations

import socket
import sys
import time

TIMEOUT_SECONDS = 60


def is_accepting(address: str) -> bool:
    kind, _, target = address.partition(":")
    try:
        if kind == "unix":
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(5)
                probe.connect(target)
        else:
            host, _, port = target.rpartition(":")
            socket.create_connection((host, int(port)), timeout=5).close()
    except OSError:
        return False
    return True


def main(address: str, timeout_seconds: float = TIMEOUT_SECONDS) -> int:
    deadline = time.monotonic() + timeout_seconds
    while not is_accepting(address):
        if time.monotonic() >= deadline:
            print("The database did not become ready. Check its service and restart the workload.", file=sys.stderr)
            return 1
        time.sleep(0.1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
