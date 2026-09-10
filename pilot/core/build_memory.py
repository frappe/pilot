from __future__ import annotations

import psutil

BUILD_MEMORY_SHARE = 0.75


def build_memory_limit_mb() -> int:
    """Memory one asset build may use before the kernel kills it, so a runaway
    build fails instead of exhausting the host."""
    return int(psutil.virtual_memory().total / (1024 * 1024) * BUILD_MEMORY_SHARE)
