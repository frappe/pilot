from __future__ import annotations

import psutil

BUILD_MEMORY_SHARE = 0.85


def build_memory_limit_mb() -> int:
    """Memory one asset build may use before the kernel kills it. A share of what
    is free rather than of total, so the limit is one the host can honour and a
    runaway build dies instead of freezing the machine."""
    return int(psutil.virtual_memory().available / (1024 * 1024) * BUILD_MEMORY_SHARE)
