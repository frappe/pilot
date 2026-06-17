from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bench_cli.config.worker_config import WorkerGroup
    from bench_cli.core.bench import Bench


# Stop timeouts for companion processes (seconds), matching legacy bench defaults.
_COMPANION_QUEUE_STOP_TIMEOUT = {
    "default": 1560,
    "long": 1560,
    "short": 360,
}
_COMPANION_SCHEDULER_TIMEOUT = 60
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
        """Gunicorn config for the socket-activated admin.

        Bound to a localhost port as a fallback; under systemd socket activation
        gunicorn inherits the listening socket via LISTEN_FDS and ignores `bind`.
        Single worker with threads so the in-app idle watchdog and SSE streams
        share one process. No preload, so create_app runs in the worker (the
        watchdog needs the arbiter as its parent)."""
        cfg = self.bench.config.admin
        self.admin_config_path.parent.mkdir(parents=True, exist_ok=True)
        self.admin_config_path.write_text(
            f'bind = "127.0.0.1:{cfg.internal_port}"\n'
            f"workers = 1\n"
            f"threads = 8\n"
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
        if not self.bench.config.production.use_companion_manager:
            return base + self._malloc_trim_hook()
        return self._render_companion_config(base)

    def _malloc_trim_hook(self) -> str:
        """A background timer thread that returns freed heap to the OS.

        glibc keeps freed allocations in per-arena free lists, so a transient
        spike (large query/report) pins the web worker's RSS at its high-water
        mark. malloc_trim(0) gives that memory back. A daemon thread (started in
        each worker via post_worker_init) trims every `malloc_trim_interval`
        seconds regardless of traffic — so an idle worker still reclaims after a
        spike — and post_request wakes it early once `malloc_trim_requests`
        requests have accrued. Either knob is disabled with 0; both 0 -> ""."""
        cfg = self.bench.config.gunicorn
        reqs, interval = cfg.malloc_trim_requests, cfg.malloc_trim_interval
        if reqs <= 0 and interval <= 0:
            return ""
        post_request = "" if reqs <= 0 else f'''

def post_request(worker, req, environ, resp):
    global _malloc_trim_count
    with _malloc_trim_lock:
        _malloc_trim_count += 1
        if _malloc_trim_count >= {reqs}:
            _malloc_trim_event.set()
'''
        return f'''

import ctypes
import threading

try:
    _libc = ctypes.CDLL("libc.so.6", use_errno=True)
    _libc.malloc_trim.argtypes = [ctypes.c_size_t]
    _libc.malloc_trim.restype = ctypes.c_int
except (OSError, AttributeError):
    _libc = None

_malloc_trim_event = threading.Event()
_malloc_trim_lock = threading.Lock()
_malloc_trim_count = 0


def _malloc_trim_loop():
    global _malloc_trim_count
    timeout = {interval} or None
    while True:
        _malloc_trim_event.wait(timeout)
        _malloc_trim_event.clear()
        with _malloc_trim_lock:
            _malloc_trim_count = 0
        if _libc is not None:
            _libc.malloc_trim(0)


def post_worker_init(worker):
    if _libc is not None:
        threading.Thread(target=_malloc_trim_loop, name="malloc-trim", daemon=True).start()
{post_request}'''

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
            + self._malloc_trim_hook()
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
        workers: list[dict] = [
            self._companion_spec(
                "scheduler",
                "frappe.gunicorn_companion:run_scheduler",
                cwd=sites_dir,
                stop_timeout=_COMPANION_SCHEDULER_TIMEOUT,
                logs_dir=logs_dir,
            )
        ]

        for group_index, group in enumerate(self.bench.config.workers.groups, start=1):
            workers.extend(self._worker_group_specs(group_index, group, sites_dir, logs_dir))

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

    def _worker_group_specs(
        self,
        group_index: int,
        group: "WorkerGroup",
        sites_dir: Path,
        logs_dir: Path,
    ) -> list[dict]:
        queue_names = ",".join(group.queues)
        stop_timeout = max(
            _COMPANION_QUEUE_STOP_TIMEOUT.get(q, _COMPANION_QUEUE_STOP_TIMEOUT["default"])
            for q in group.queues
        )
        name_slug = "-".join(group.queues)
        return [
            self._companion_spec(
                f"worker-{name_slug}-{i}",
                "frappe.gunicorn_companion:run_worker",
                cwd=sites_dir,
                stop_timeout=stop_timeout,
                logs_dir=logs_dir,
                env={"FRAPPE_COMPANION_QUEUE": queue_names},
            )
            for i in range(1, group.count + 1)
        ]

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

    def upstream_server(self) -> str:
        return self._bind()
