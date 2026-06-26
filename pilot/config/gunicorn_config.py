from dataclasses import dataclass


@dataclass
class GunicornConfig:
    workers: int = 2
    threads: int = 8
    timeout: int = 120
    worker_class: str = "gthread"
    malloc_arena_max: int = 2  # cap glibc malloc arenas; 0 = unset
    max_requests: int = 2000  # recycle web worker after N requests to release heap; 0 = disabled
    max_requests_jitter: int = 500
