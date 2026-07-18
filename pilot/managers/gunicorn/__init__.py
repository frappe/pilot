from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.internal.template import Template

if TYPE_CHECKING:
    from pilot.core.bench import Bench

_CONFIG_TEMPLATE = Template.from_path(Path(__file__).parent / "templates" / "gunicorn.conf.py.template")


# Stop timeouts for companion processes (seconds), matching legacy bench defaults.
_COMPANION_QUEUE_STOP_TIMEOUT = {
    "default": 1560,
    "long": 1560,
    "short": 360,
}
_COMPANION_SOCKETIO_TIMEOUT = 30


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
            f"preload_app = False\n"
        )

    def _render_config(self) -> str:
        cfg = self.bench.config.gunicorn
        worker_class = cfg.worker_class
        # gthread is required for threads to actually be used.
        if cfg.threads > 0 and worker_class == "sync":
            worker_class = "gthread"
        companion = self.bench.config.production.use_companion_manager
        workers = self._build_companion_workers(self.bench.sites_path, self.bench.logs_path) if companion else []
        return _CONFIG_TEMPLATE.render(
            bind=self._bind(),
            workers=cfg.workers,
            threads=cfg.threads,
            worker_class=worker_class,
            timeout=cfg.timeout,
            max_requests=cfg.max_requests,
            max_requests_jitter=cfg.max_requests_jitter,
            companion=companion,
            control_socket=self.bench.config_path / "gunicorn-companion.sock",
            companion_workers=repr(workers),
        )

    def _build_companion_workers(self, sites_dir: Path, logs_dir: Path) -> list[dict]:
        # A single RQ worker-pool runs all queues; the Frappe scheduler runs as a
        # thread inside the pool workers, so it needs no companion of its own.
        workers: list[dict] = [self._worker_pool_spec(sites_dir, logs_dir)]

        if self._is_socketio_companion_enabled():
            workers.append(
                self._companion_spec(
                    "socketio",
                    "frappe.gunicorn_companion:run_socketio",
                    cwd=self.bench.path,
                    stop_timeout=_COMPANION_SOCKETIO_TIMEOUT,
                    logs_dir=logs_dir,
                )
            )

        return workers

    def _worker_pool_spec(self, sites_dir: Path, logs_dir: Path) -> dict:
        groups = self.bench.config.workers.groups
        queues: list[str] = []
        for group in groups:
            for queue in group.queues:
                if queue not in queues:
                    queues.append(queue)
        num_workers = max(1, sum(group.count for group in groups))
        stop_timeout = max(
            (_COMPANION_QUEUE_STOP_TIMEOUT.get(q, _COMPANION_QUEUE_STOP_TIMEOUT["default"]) for q in queues),
            default=_COMPANION_QUEUE_STOP_TIMEOUT["default"],
        )
        return self._companion_spec(
            "worker-pool",
            "frappe.gunicorn_companion:run_worker_pool",
            cwd=sites_dir,
            stop_timeout=stop_timeout,
            logs_dir=logs_dir,
            env={
                "FRAPPE_COMPANION_QUEUE": ",".join(queues),
                "FRAPPE_COMPANION_NUM_WORKERS": str(num_workers),
            },
        )

    def _companion_spec(
        self,
        name: str,
        target: str,
        *,
        cwd: Path,
        stop_timeout: int,
        logs_dir: Path,
        env: dict | None = None,
    ) -> dict:
        spec: dict = {
            "name": name,
            "target": target,
            "cwd": str(cwd),
            "stop_timeout": stop_timeout,
            "stdout": str(logs_dir / f"{name}.log"),
            "stderr": "stdout",
        }
        if env:
            spec["env"] = env
        return spec

    def _is_socketio_companion_enabled(self) -> bool:
        if self.bench.config.socketio_backend == "python":
            return True
        return bool(shutil.which("node") or shutil.which("nodejs"))

    def _bind(self) -> str:
        return f"127.0.0.1:{self.bench.config.http_port}"

    @property
    def upstream_server(self) -> str:
        return self._bind()
