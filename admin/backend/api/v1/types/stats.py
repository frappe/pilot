from __future__ import annotations

from typing import NotRequired, TypedDict

ChartRow = dict[str, float | None]


class LogFileStatus(TypedDict):
    description: str
    path: str
    last_modified: str | None


class DiskUsage(TypedDict):
    used_bytes: int | None
    total_bytes: int | None
    percent: float | None


class StorageSnapshot(TypedDict):
    disk: DiskUsage


class SystemMetricsHistory(TypedDict):
    earliest: int | None
    points: list[ChartRow]
    storage: StorageSnapshot | None
    memory_total_bytes: int | None


class ApplicationMetricsHistory(TypedDict):
    earliest: int | None
    services: list[str]
    cpu: list[ChartRow]
    memory: list[ChartRow]


class SystemHistory(TypedDict):
    window: str
    window_seconds: int
    now: int
    system: SystemMetricsHistory
    application: ApplicationMetricsHistory


class SlowQueryOverview(TypedDict):
    enabled: bool
    unsupported: NotRequired[bool]
    sites: list[str]
    counts: list[ChartRow]
    durations: list[ChartRow]
    queries: NotRequired[list[str]]
    query_counts: NotRequired[list[ChartRow]]


class DatabaseHistory(TypedDict):
    window: str
    window_seconds: int
    now: int
    earliest: int | None
    points: list[ChartRow]
    slow_queries: SlowQueryOverview


class WafTotals(TypedDict):
    flagged: int
    blocked: int
    would_block: int


class WafRuleCount(TypedDict):
    id: str
    message: str
    count: int


class WafAddressCount(TypedDict):
    ip: str
    count: int


class WafSeriesPoint(TypedDict):
    t: int
    flagged: int
    blocked: int


class WafAnalytics(TypedDict):
    window: str
    window_seconds: int
    now: int
    mode: str
    log_present: bool
    totals: WafTotals
    top_rules: list[WafRuleCount]
    top_ips: list[WafAddressCount]
    series: list[WafSeriesPoint]


class SystemInfo(TypedDict):
    disk_total: int
    cpu_count: int | None
    memory_total: int
    swap_total: int
    kernel_version: str
    os_version: str
    runtime: dict[str, str]


class CpuBreakdown(TypedDict):
    user: float
    system: float
    iowait: float
    irq: float
    other: float
    idle: float


class MemoryBreakdown(TypedDict):
    used_bytes: int
    cached_bytes: int
    free_bytes: int
    swap_used_bytes: int


class NetworkRates(TypedDict):
    rx_bytes_per_sec: float
    tx_bytes_per_sec: float


class DiskIoRates(TypedDict):
    read_bytes_per_sec: float
    write_bytes_per_sec: float


class PathSize(TypedDict):
    label: str
    path: str
    used_bytes: int


class SystemMetrics(TypedDict):
    cpu_percent: float
    cpu_count: int | None
    cpu_breakdown: CpuBreakdown
    load_avg: tuple[float, float, float]
    memory_percent: float
    memory_used: int
    memory_total: int
    memory_breakdown: MemoryBreakdown
    disk_percent: float
    disk_used: int
    disk_total: int
    network: NetworkRates
    disk_io: DiskIoRates
    paths: list[PathSize]
