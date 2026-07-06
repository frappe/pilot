from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pilot.config.mariadb_config import (
    innodb_buffer_pool_bounds_mb,
    ram_for_mariadb_mb,
    recommended_innodb_buffer_pool_size_mb,
)
from pilot.utils import iter_sibling_benches


@dataclass(frozen=True)
class MariaDBMemoryPlan:
    total_mb: int
    ram_for_mariadb_mb: int
    allocated_to_other_benches_mb: int
    min_mb: int
    max_mb: int
    recommended_mb: int


def host_memory_mb() -> int:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size / 1024 / 1024)
    except (AttributeError, OSError, ValueError):
        return 4096


def memory_plan_for_bench(bench_root: Path, total_mb: int | None = None) -> MariaDBMemoryPlan:
    total = int(total_mb or host_memory_mb())
    ram_for_db = int(ram_for_mariadb_mb(total))
    min_mb, ratio_max_mb = innodb_buffer_pool_bounds_mb(total)
    allocated_elsewhere = sum(
        int(config.mariadb.innodb_buffer_pool_size_mb or 0)
        for _, config in iter_sibling_benches(bench_root)
    )
    host_available_max = max(1, ram_for_db - allocated_elsewhere)
    max_mb = max(1, min(ratio_max_mb, host_available_max))
    recommended = min(recommended_innodb_buffer_pool_size_mb(total), max_mb)
    if max_mb >= min_mb:
        recommended = max(min_mb, recommended)
    return MariaDBMemoryPlan(
        total_mb=total,
        ram_for_mariadb_mb=ram_for_db,
        allocated_to_other_benches_mb=allocated_elsewhere,
        min_mb=min_mb,
        max_mb=max_mb,
        recommended_mb=recommended,
    )


def validate_innodb_buffer_pool_size(bench_root: Path, value_mb: int) -> str | None:
    plan = memory_plan_for_bench(bench_root)
    if value_mb < plan.min_mb:
        return f"InnoDB buffer pool size cannot be less than {plan.min_mb}MB."
    if value_mb > plan.max_mb:
        return (
            f"InnoDB buffer pool size cannot be greater than {plan.max_mb}MB "
            f"for the available host memory."
        )
    return None
