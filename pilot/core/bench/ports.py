from __future__ import annotations

import socket
from pathlib import Path

# A Vite dev server started by the frappe-ui plugin listens on 8080 + offset.
_FRONTEND_BASE_PORT = 8080


def pick_port_offset(benches_root: Path) -> int:
    """First port offset no bench or worktree under `benches_root` claims and no live process holds."""
    used = _configured_offsets(benches_root)
    offset = 0
    while offset in used or any(_port_is_live(port) for port in _offset_ports(offset)):
        offset += 1
    return offset


def _configured_offsets(benches_root: Path) -> set[int]:
    from pilot.config import BenchConfig

    base_http_port = BenchConfig.default_ports()["http_port"]
    used: set[int] = set()
    for toml_path in sorted(benches_root.glob(f"*/{BenchConfig.FILENAME}")):
        try:
            config = BenchConfig.read(toml_path.parent, validate=False)
        except Exception:
            # Half-configured benches still count when they parse, others cannot.
            continue
        used.add(config.http_port - base_http_port)
        used.update(worktree.port_offset for worktree in config.worktrees)
    return used


def _offset_ports(offset: int) -> list[int]:
    from pilot.config import BenchConfig

    bases = BenchConfig.default_ports()
    admin_internal_port = bases["admin.port"] + 1
    return [
        *(base + offset for base in bases.values()),
        admin_internal_port + offset,
        _FRONTEND_BASE_PORT + offset,
    ]


def _port_is_live(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False
