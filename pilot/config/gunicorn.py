from dataclasses import dataclass

from pilot.exceptions import ConfigError


@dataclass
class GunicornConfig:
    workers: int = 2
    threads: int = 8
    timeout: int = 120
    worker_class: str = "gthread"
    malloc_arena_max: int = 2  # cap glibc malloc arenas; 0 = unset
    max_requests: int = 2000  # recycle web worker after N requests to release heap; 0 = disabled
    max_requests_jitter: int = 500

    @classmethod
    def from_dict(cls, data: dict) -> "GunicornConfig":
        d = cls()
        return cls(
            workers=data.get("workers", d.workers),
            threads=data.get("threads", d.threads),
            timeout=data.get("timeout", d.timeout),
            worker_class=data.get("worker_class", d.worker_class),
            malloc_arena_max=data.get("malloc_arena_max", d.malloc_arena_max),
            max_requests=data.get("max_requests", d.max_requests),
            max_requests_jitter=data.get("max_requests_jitter", d.max_requests_jitter),
        )

    def validate(self) -> None:
        if not isinstance(self.workers, int) or self.workers < 1:
            raise ConfigError(f"gunicorn.workers must be a positive integer, got '{self.workers}'.")
        if not isinstance(self.threads, int) or self.threads < 1:
            raise ConfigError(f"gunicorn.threads must be a positive integer, got '{self.threads}'.")
        if not isinstance(self.timeout, int) or self.timeout < 1:
            raise ConfigError(f"gunicorn.timeout must be a positive integer, got '{self.timeout}'.")
        if not self.worker_class:
            raise ConfigError("gunicorn.worker_class must not be empty.")
        if not isinstance(self.malloc_arena_max, int) or self.malloc_arena_max < 0:
            raise ConfigError(
                f"gunicorn.malloc_arena_max must be a non-negative integer, got '{self.malloc_arena_max}'."
            )
        if not isinstance(self.max_requests, int) or self.max_requests < 0:
            raise ConfigError(
                f"gunicorn.max_requests must be a non-negative integer, got '{self.max_requests}'."
            )
        if not isinstance(self.max_requests_jitter, int) or self.max_requests_jitter < 0:
            raise ConfigError(
                f"gunicorn.max_requests_jitter must be a non-negative integer, got '{self.max_requests_jitter}'."
            )
