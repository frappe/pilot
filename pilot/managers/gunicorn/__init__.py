from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pilot.internal.template import Template

if TYPE_CHECKING:
    from pilot.core.bench import Bench

_CONFIG_TEMPLATE = Template.from_path(Path(__file__).parent / "templates" / "gunicorn.conf.py.template")


class GunicornManager:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    @property
    def config_path(self) -> Path:
        return self.bench.config_path / "gunicorn.conf.py"

    @property
    def admin_config_path(self) -> Path:
        return self.bench.config_path / "admin-gunicorn.conf.py"

    def generate_config(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(self._render_config())

    def generate_admin_config(self) -> None:
        """Write admin Gunicorn config for socket activation."""
        cfg = self.bench.config.admin
        self.admin_config_path.parent.mkdir(parents=True, exist_ok=True)
        self.admin_config_path.write_text(
            f'bind = "127.0.0.1:{cfg.internal_port}"\n'
            f"workers = 1\n"
            f"threads = 4\n"
            f'worker_class = "gthread"\n'
            f"timeout = 120\n"
            # Requests in flight during a restart get this long. Event streams end at once:
            # admin.backend.draining closes them when the stop begins.
            f"graceful_timeout = 30\n"
            f"preload_app = False\n"
        )

    def _render_config(self) -> str:
        cfg = self.bench.config.gunicorn
        worker_class = cfg.worker_class
        # gthread is required for threads to actually be used.
        if cfg.threads > 0 and worker_class == "sync":
            worker_class = "gthread"
        return _CONFIG_TEMPLATE.render(
            bind=self._bind(),
            workers=cfg.workers,
            threads=cfg.threads,
            worker_class=worker_class,
            timeout=cfg.timeout,
            max_requests=cfg.max_requests,
            max_requests_jitter=cfg.max_requests_jitter,
        )

    def _bind(self) -> str:
        return f"127.0.0.1:{self.bench.config.http_port}"

    @property
    def upstream_server(self) -> str:
        return self._bind()
