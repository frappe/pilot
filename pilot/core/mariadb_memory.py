from __future__ import annotations

from dataclasses import dataclass

_HARDWARE_MEMORY_FACTOR = 0.972
_HARDWARE_MEMORY_RESERVE_MB = 218
_MARIADB_OPERATING_RESERVE_MB = 700
_MEMORY_PER_CONNECTION_MB = 35
_MIN_BUFFER_POOL_MB = 128
_MIN_MEMORY_MAX_MB = 512
_MIN_MEMORY_LIMIT_GAP_MB = 128
_MEMORY_HIGH_SHARE = 0.85
_MEMORY_SWAP_SHARE = 0.20
_MIN_MEMORY_SWAP_MB = 100
_MAX_MEMORY_SWAP_MB = 1024
_MAX_HOST_MEMORY_SHARE = 0.5
_MIN_MAX_CONNECTIONS = 10
_MIN_BUFFER_POOL_SHARE = 0.2
_MAX_BUFFER_POOL_SHARE = 0.7
_BUFFER_POOL_MAX_BLOCK_MB = 8


@dataclass(frozen=True)
class MariaDBMemorySizing:
    """Startup values for MariaDB instance"""

    total_memory_mb: int
    mariadb_memory_mb: int
    innodb_buffer_pool_mb: int
    max_connections: int
    key_buffer_mb: int
    innodb_log_file_mb: int
    memory_high_mb: int
    memory_max_mb: int
    memory_swap_max_mb: int


@dataclass(frozen=True)
class MariaDBVariableLimits:
    """Safe user-configurable ranges within Pilot's MariaDB service budget."""

    innodb_buffer_pool_min_mb: int
    innodb_buffer_pool_max_mb: int
    innodb_buffer_pool_recommended_mb: int
    max_connections_min: int
    max_connections_max: int
    max_connections_recommended: int


def calculate_mariadb_memory(total_memory_mb: int) -> MariaDBMemorySizing:
    if total_memory_mb <= 0:
        raise ValueError("total_memory_mb must be greater than zero")

    real_memory_mb = _HARDWARE_MEMORY_FACTOR * total_memory_mb - _HARDWARE_MEMORY_RESERVE_MB
    mariadb_memory_mb = real_memory_mb / 2 - _MARIADB_OPERATING_RESERVE_MB

    recommended_connections = 5 * round(total_memory_mb / 1024)
    max_connections = max(50, recommended_connections)
    key_buffer_mb = 128 if mariadb_memory_mb > 4096 else 32
    base_memory_mb = key_buffer_mb + 32 + 16

    connection_aware_pool_mb = int(
        mariadb_memory_mb - base_memory_mb - recommended_connections * _MEMORY_PER_CONNECTION_MB
    )
    percentage_pool_mb = int(mariadb_memory_mb * 0.65)
    innodb_buffer_pool_mb = max(
        _MIN_BUFFER_POOL_MB,
        min(connection_aware_pool_mb, percentage_pool_mb),
    )

    # Pilot shares the VM with web, worker, and Redis processes. Keep the
    # startup floor, but never let it claim more than half of a small host.
    host_share_cap_mb = max(1, int(total_memory_mb * _MAX_HOST_MEMORY_SHARE))
    memory_max_mb = min(
        host_share_cap_mb,
        max(_MIN_MEMORY_MAX_MB, round(mariadb_memory_mb)),
    )
    # Keep MemoryHigh close enough to MemoryMax that normal MariaDB peaks do
    # not trigger sustained cgroup throttling while retaining a safety margin.
    memory_high_mb = max(
        1,
        min(
            memory_max_mb - _MIN_MEMORY_LIMIT_GAP_MB,
            round(memory_max_mb * _MEMORY_HIGH_SHARE),
        ),
    )
    # Give MariaDB bounded swap headroom that scales with its service budget.
    # A fixed 100 MB ceiling can be exhausted during short-lived memory peaks.
    memory_swap_max_mb = min(
        _MAX_MEMORY_SWAP_MB,
        max(_MIN_MEMORY_SWAP_MB, round(memory_max_mb * _MEMORY_SWAP_SHARE)),
    )

    return MariaDBMemorySizing(
        total_memory_mb=total_memory_mb,
        mariadb_memory_mb=max(0, int(mariadb_memory_mb)),
        innodb_buffer_pool_mb=innodb_buffer_pool_mb,
        max_connections=max_connections,
        key_buffer_mb=key_buffer_mb,
        innodb_log_file_mb=_innodb_log_file_size(total_memory_mb),
        memory_high_mb=memory_high_mb,
        memory_max_mb=memory_max_mb,
        memory_swap_max_mb=memory_swap_max_mb,
    )


def calculate_mariadb_variable_limits(total_memory_mb: int) -> MariaDBVariableLimits:
    """Adapt Press's MariaDB guards to Pilot's shared-VM memory ceiling."""
    sizing = calculate_mariadb_memory(total_memory_mb)
    pool_min_mb = min(
        sizing.innodb_buffer_pool_mb,
        max(
            _MIN_BUFFER_POOL_MB,
            _round_up(
                int(sizing.memory_max_mb * _MIN_BUFFER_POOL_SHARE),
                _BUFFER_POOL_MAX_BLOCK_MB,
            ),
        ),
    )
    pool_max_mb = max(
        pool_min_mb,
        _round_down(int(sizing.memory_max_mb * _MAX_BUFFER_POOL_SHARE), _BUFFER_POOL_MAX_BLOCK_MB),
    )
    return MariaDBVariableLimits(
        innodb_buffer_pool_min_mb=pool_min_mb,
        innodb_buffer_pool_max_mb=pool_max_mb,
        innodb_buffer_pool_recommended_mb=min(
            pool_max_mb,
            max(pool_min_mb, sizing.innodb_buffer_pool_mb),
        ),
        max_connections_min=_MIN_MAX_CONNECTIONS,
        max_connections_max=max(
            sizing.max_connections,
            sizing.memory_max_mb // _MEMORY_PER_CONNECTION_MB,
        ),
        max_connections_recommended=sizing.max_connections,
    )


def _innodb_log_file_size(total_memory_mb: int) -> int:
    ram_gb = round(total_memory_mb / 1024)
    if ram_gb > 16:
        return 2048
    if ram_gb > 8:
        return 1024
    if ram_gb > 4:
        return 512
    if ram_gb > 2:
        return 128
    return 48


def _round_up(value: int, block: int) -> int:
    return ((value + block - 1) // block) * block


def _round_down(value: int, block: int) -> int:
    return (value // block) * block
