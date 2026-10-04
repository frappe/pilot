from __future__ import annotations

from pathlib import Path

from pilot.exceptions import BenchError

BUILD_MEMORY_SHARE = 0.85
MIN_BUILD_MEMORY_MB = 512


def can_read_memory(meminfo: Path = Path("/proc/meminfo")) -> bool:
    """Whether this host reports its free memory. Linux does; macOS has no /proc."""
    return meminfo.exists()


def available_memory_mb(meminfo: Path = Path("/proc/meminfo")) -> int:
    """Memory the kernel can hand out without swapping, in MB. Read from /proc instead."""
    for line in meminfo.read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise BenchError(f"{meminfo} has no MemAvailable line.")


def build_memory_limit_mb(override_mb: int = 0) -> int:
    """Max memory in MB one asset build may use, so a runaway build
    is killed before it eats all free memory and crashes the host.
    Refuses to build if too little memory is free to begin with.
    A positive override_mb (bench.toml's [build] memory_limit_mb) is used as-is."""
    if override_mb:
        return override_mb
    limit_mb = int(available_memory_mb() * BUILD_MEMORY_SHARE)
    if limit_mb < MIN_BUILD_MEMORY_MB:
        raise BenchError(
            f"Not enough free memory to build: only {limit_mb}MB available, "
            f"need at least {MIN_BUILD_MEMORY_MB}MB."
        )
    return limit_mb
