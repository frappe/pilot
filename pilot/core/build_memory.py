from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pilot.exceptions import BenchError

BUILD_MEMORY_SHARE = 0.85
# Installed by install.sh's root phase; adds a swap file while a build runs.
BUILD_SWAP_HELPER = Path("/usr/local/sbin/pilot-build-swap")
# A large frontend build (Helpdesk) needs more than 2 GB.
BUILD_SWAP_BELOW_MB = 3072


def can_read_memory(meminfo: Path = Path("/proc/meminfo")) -> bool:
    """Whether this host reports its free memory. Linux does; macOS has no /proc."""
    return meminfo.exists()


def available_memory_mb(meminfo: Path = Path("/proc/meminfo")) -> int:
    """Memory the kernel can hand out without swapping, in MB. Read from /proc instead."""
    for line in meminfo.read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise BenchError(f"{meminfo} has no MemAvailable line.")


def free_swap_mb(meminfo: Path = Path("/proc/meminfo")) -> int:
    """Unused swap in MB; 0 on a host without swap."""
    for line in meminfo.read_text().splitlines():
        if line.startswith("SwapFree:"):
            return int(line.split()[1]) // 1024
    return 0


def build_swap_limit_mb() -> int:
    """Max swap in MB one asset build may use past its RAM cap: the same share of
    free swap, so other services keep the rest."""
    return int(free_swap_mb() * BUILD_MEMORY_SHARE)


def build_memory_limit_mb(override_mb: int = 0) -> int:
    """Max memory in MB one asset build may use, so a runaway build
    is killed before it eats all free memory and crashes the host.
    A positive override_mb (bench.toml's [build] memory_limit_mb) is used as-is."""
    if override_mb:
        return override_mb
    return int(available_memory_mb() * BUILD_MEMORY_SHARE)


@contextmanager
def build_swap() -> Iterator[None]:
    """Add swap while a build runs on a host with little free memory. Without the
    install.sh helper, the build runs without it."""
    if not BUILD_SWAP_HELPER.exists() or not can_read_memory() or available_memory_mb() >= BUILD_SWAP_BELOW_MB:
        yield
        return
    added = _has_switched_build_swap("on")
    try:
        yield
    finally:
        if added:
            _has_switched_build_swap("off")


def _has_switched_build_swap(action: str) -> bool:
    """Run the helper. Swap is optional, so a failure only warns."""
    try:
        result = subprocess.run(
            ["sudo", "-n", str(BUILD_SWAP_HELPER), action], capture_output=True, text=True, timeout=120
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        logging.warning("Could not turn build swap %s: %s", action, error)
        return False
    if result.returncode != 0:
        logging.warning("Could not turn build swap %s: %s", action, result.stderr.strip())
    return result.returncode == 0
