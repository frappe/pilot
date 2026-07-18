from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pilot.core.bench import Bench


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
        base = (
            f'bind = "{self._bind()}"\n'
            f"workers = {cfg.workers}\n"
            f"threads = {cfg.threads}\n"
            f'worker_class = "{worker_class}"\n'
            f"timeout = {cfg.timeout}\n"
            f"preload_app = True\n"
        )
        if cfg.max_requests > 0:
            base += f"max_requests = {cfg.max_requests}\n"
            base += f"max_requests_jitter = {cfg.max_requests_jitter}\n"
        if not self.bench.config.production.use_companion_manager:
            return base
        return self._render_companion_config(base)

    def _render_companion_config(self, base: str) -> str:
        sites_dir = self.bench.sites_path
        logs_dir = self.bench.logs_path
        control_socket = self.bench.config_path / "gunicorn-companion.sock"
        workers_code = self._render_companion_workers(sites_dir, logs_dir)

        return (
            "import os\n\n"
            "# Allow the Python socketio companion to run gevent by skipping\n"
            "# frappe.app's eager mysqlclient import before preload.\n"
            'os.environ.setdefault("FRAPPE_GUNICORN_COMPANION", "1")\n\n'
            'wsgi_app = "frappe.app:application"\n'
            "\n"
            + base
            + "graceful_timeout = 30\n"
            + f'companion_control_socket = "{control_socket}"\n'
            + "companion_control_socket_mode = 0o660\n"
            + "companion_manager_shutdown_buffer = 15\n"
            + "\n"
            + f"companion_workers = {workers_code}\n"
            + "\n\n"
            + "def on_starting(server):\n"
            + "    import frappe.gunicorn_companion as companion\n"
            + "    companion.warmup()\n"
            + "\n\n"
            + "def when_ready(server):\n"
            + "    from frappe._optimizations import freeze_gc\n"
            + "    freeze_gc()\n"
        )

    def _render_companion_workers(self, sites_dir: Path, logs_dir: Path) -> str:
        workers = self._build_companion_workers(sites_dir, logs_dir)
        lines = ["["]
        for i, worker in enumerate(workers):
            comma = "," if i < len(workers) - 1 else ""
            lines.append(self._render_worker_dict(worker) + comma)
        lines.append("]")
        return "\n".join(lines)

    def _render_worker_dict(self, worker: dict) -> str:
        items = []
        for key, value in worker.items():
            items.append(self._render_dict_item(key, value))
        return "    {\n" + ",\n".join(items) + "\n    }"

    @staticmethod
    def _render_dict_item(key: str, value) -> str:
        if isinstance(value, str):
            return f'        "{key}": "{value}"'
        if isinstance(value, dict):
            inner = ", ".join(f'"{k}": "{v}"' for k, v in value.items())
            return f'        "{key}": {{{inner}}}'
        return f'        "{key}": {value}'

    def _build_companion_workers(self, sites_dir: Path, logs_dir: Path) -> list[dict]:
        # A single RQ worker-pool runs all queues; the Frappe scheduler runs as a
        # thread inside the pool workers, so it needs no companion of its own.
        workers: list[dict] = [self._worker_pool_spec(sites_dir, logs_dir)]

        if self._socketio_companion_enabled():
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

    def _socketio_companion_enabled(self) -> bool:
        if self.bench.config.socketio_backend == "python":
            return True
        return bool(shutil.which("node") or shutil.which("nodejs"))

    def _bind(self) -> str:
        return f"127.0.0.1:{self.bench.config.http_port}"

    @property
    def upstream_server(self) -> str:
        return self._bind()
