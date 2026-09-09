from __future__ import annotations

from dataclasses import dataclass

_BUILD_SHARE_OF_TOTAL = 0.75
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
    """Budget a build gets. A full build peaks near 1.6GB - several compilers,
    yarn and node at once - so the share bounds it on a big host, and what is
    free right now bounds it on a loaded one."""
    if total_memory_mb <= 0:
        raise ValueError("total_memory_mb must be greater than zero")

    share_mb = int(total_memory_mb * _BUILD_SHARE_OF_TOTAL)
    headroom_mb = available_memory_mb - _OOM_RESERVE_MB
    limit_mb = min(share_mb, headroom_mb)
    return BuildMemorySizing(
        limit_mb=max(limit_mb, 0),
        can_build=limit_mb >= _MIN_BUILD_MEMORY_MB,
    )
