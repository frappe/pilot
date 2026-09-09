from __future__ import annotations

from dataclasses import dataclass

# A full build peaks around 1.1GB: several compilers, yarn and node at once.
_BUILD_SHARE_OF_TOTAL = 0.6
_OOM_RESERVE_MB = 50
_MIN_BUILD_MEMORY_MB = 1024


@dataclass(frozen=True)
class BuildMemorySizing:
    """How much memory one asset build may use, and whether it can run at all."""

    limit_mb: int
    can_build: bool

    @property
    def refusal_reason(self) -> str:
        return (
            f"Not enough free memory to build: {self.limit_mb}MB available for the build, "
            f"{_MIN_BUILD_MEMORY_MB}MB needed. Stop other work and retry."
        )


def calculate_build_memory(total_memory_mb: int, available_memory_mb: int) -> BuildMemorySizing:
    """Budget a build gets. The share bounds it on a big host; what is free right
    now bounds it on a loaded one, so a build inside its budget cannot still tip
    the machine over."""
    if total_memory_mb <= 0:
        raise ValueError("total_memory_mb must be greater than zero")

    share_mb = int(total_memory_mb * _BUILD_SHARE_OF_TOTAL)
    headroom_mb = available_memory_mb - _OOM_RESERVE_MB
    limit_mb = min(share_mb, headroom_mb)
    return BuildMemorySizing(
        limit_mb=max(limit_mb, 0),
        can_build=limit_mb >= _MIN_BUILD_MEMORY_MB,
    )
