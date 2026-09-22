from __future__ import annotations

import psutil

from pilot.exceptions import BenchError

BUILD_MEMORY_SHARE = 0.85
MIN_BUILD_MEMORY_MB = 512


def build_memory_limit_mb(override_mb: int = 0) -> int:
    """Max memory in MB one asset build may use, so a runaway build
    is killed before it eats all free memory and crashes the host.
    Refuses to build if too little memory is free to begin with.
    A positive override_mb (bench.toml's [build] memory_limit_mb) is used as-is."""
    if override_mb:
        return override_mb
    limit_mb = int(psutil.virtual_memory().available / (1024 * 1024) * BUILD_MEMORY_SHARE)
    if limit_mb < MIN_BUILD_MEMORY_MB:
        raise BenchError(
            f"Not enough free memory to build: only {limit_mb}MB available, "
            f"need at least {MIN_BUILD_MEMORY_MB}MB."
        )
    return limit_mb
