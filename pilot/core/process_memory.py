from __future__ import annotations

from dataclasses import dataclass

_OOM_RESERVE_MB = 50
_HEADROOM_MULTIPLIER = 3.0
_THROTTLE_SHARE = 0.7
_MEASURED_PEAK_MB = {
    "web": 141,
    "worker_pool": 118,
    "socketio": 60,
}
_MIN_LIMIT_MB = 128


@dataclass(frozen=True)
class ProcessMemorySizing:
    """Soft and hard memory limits for one bench process."""

    memory_high_mb: int
    memory_max_mb: int


def calculate_process_memory(
    name: str,
    total_memory_mb: int,
    database_memory_max_mb: int,
) -> ProcessMemorySizing | None:
    """Limits for a long-running bench process, or None where it has no measured
    profile. Sized off observed peaks with room to grow, then trimmed to the share
    of the host the database and the kernel reserve leave behind.

    These are ceilings on a runaway, not reservations: the whole set idles near
    300MB, so on a small host the ceilings may sum past total memory. Sizing them
    to sum within it would put every service below its own working set."""
    if total_memory_mb <= 0:
        raise ValueError("total_memory_mb must be greater than zero")

    peak_mb = _MEASURED_PEAK_MB.get(name)
    if peak_mb is None:
        return None

    budget_mb = total_memory_mb - database_memory_max_mb - _OOM_RESERVE_MB
    share_mb = int(budget_mb * peak_mb / sum(_MEASURED_PEAK_MB.values()))
    # Never below twice the observed peak: a limit that tight restart-loops the
    # service, which is worse than the leak it would catch.
    floor_mb = max(_MIN_LIMIT_MB, peak_mb * 2)
    memory_max_mb = max(floor_mb, min(int(peak_mb * _HEADROOM_MULTIPLIER), share_mb))
    return ProcessMemorySizing(
        memory_high_mb=max(1, int(memory_max_mb * _THROTTLE_SHARE)),
        memory_max_mb=memory_max_mb,
    )
