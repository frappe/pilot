from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.managers.environment import AdminEnvManager
from pilot.utils import cli_root

if TYPE_CHECKING:
    from pilot.core.bench import Bench


@dataclass
class ProcessDefinition:
    name: str
    argv: list[str]  # executable + args - no shell, no `cd`, no inline env prefix
    log_file: Path
    env: dict = field(default_factory=dict)
    working_dir: Path | None = None  # was `cd {dir} &&`
    stop_timeout: int | None = None  # graceful-stop seconds (redis=300, web+companion=1600)
    critical: bool = True  # dev runner stops the whole bench when this process exits


class ProcessDefinitionBuilder:
    def __init__(self, bench: "Bench", python: Path, watch_admin_js: bool) -> None:
        self.bench = bench
        self.python = python
        self.watch_admin_js = watch_admin_js

    def prod_process_definitions(self) -> list[ProcessDefinition]:
        if self.bench.config.production.use_companion_manager:
            defs = [self.web_definition(), self.admin_definition()]
        elif self.bench.config.production.process_manager == "systemd":
            all_queues = ",".join(q for group in self.bench.config.workers.groups for q in group.queues)
            num_workers = sum(group.count for group in self.bench.config.workers.groups)
            defs = [
                self.web_definition(),
                self.socketio_definition(),
                self.admin_definition(),
                self.worker_pool_definition(all_queues, num_workers),
            ]
        else:
            worker_defs = [
                pd
                for group in self.bench.config.workers.groups
                for pd in self.worker_definitions(",".join(group.queues), group.count)
            ]
            defs = [
                self.web_definition(),
                self.socketio_definition(),
                self.admin_definition(),
                *worker_defs,
            ]
        defs.append(self.redis_definition("redis_cache", "redis_cache.conf"))
        defs.append(self.redis_definition("redis_queue", "redis_queue.conf"))
        return defs

    def process_definitions(self) -> list[ProcessDefinition]:
        defs = [self.to_dev(pd) for pd in self.prod_process_definitions()]
        if self.bench.config.watch_apps_js:
            defs.append(self.watch_definition())
        if self.watch_admin_js:
            defs.append(self.admin_frontend_dev_definition())
        return defs

    def to_dev(self, pd: ProcessDefinition) -> ProcessDefinition:
        if pd.name == "admin":
            return (
                self.watch_admin_definition()
                if self.watch_admin_js
                else self.build_admin_definition("--no-timeout")
            )
        if pd.name == "web":
            return self.web_definition(dev=True)
        return pd

    def py_memory_env(self) -> dict:
        arenas = self.bench.config.gunicorn.malloc_arena_max
        if arenas and arenas > 0:
            return {"MALLOC_ARENA_MAX": str(arenas)}
        return {}

    def web_definition(self, dev: bool = False) -> ProcessDefinition:
        sites = self.bench.sites_path
        if dev:
            port = self.bench.config.http_port
            argv = [
                str(self.python),
                "-m",
                "frappe.utils.bench_helper",
                "frappe",
                "serve",
                "--port",
                str(port),
            ]
            if not self.bench.config.reload_python:
                argv.append("--noreload")
            return ProcessDefinition(
                name="web",
                argv=argv,
                log_file=self.bench.logs_path / "web.log",
                env={"DEV_SERVER": "1"},
                working_dir=sites,
            )
        gunicorn = self.bench.env_path / "bin" / "gunicorn"
        companion = self.bench.config.production.use_companion_manager
        return ProcessDefinition(
            name="web",
            argv=[str(gunicorn), "-c", "../config/gunicorn.conf.py", "frappe.app:application"],
            log_file=self.bench.logs_path / "web.log",
            env=self.py_memory_env(),
            working_dir=sites,
            stop_timeout=1600 if companion else None,
        )

    def socketio_definition(self) -> ProcessDefinition:
        if self.bench.config.socketio_backend == "python":
            argv = [str(self.python), "-m", "frappe.realtime.server"]
            working_dir = self.bench.path
            backend_env = self.py_memory_env()
        else:
            argv = ["node", f"{self.bench.apps_path}/frappe/socketio.js"]
            working_dir = self.bench.sites_path
            backend_env = {}
        return ProcessDefinition(
            name="socketio",
            argv=argv,
            log_file=self.bench.logs_path / "socketio.log",
            env=backend_env,
            working_dir=working_dir,
        )

    def watch_definition(self) -> ProcessDefinition:
        # Non-critical: frappe watch dies when the initial esbuild build fails
        # (e.g. unbuilt assets on a fresh bench); the bench must outlive it.
        return ProcessDefinition(
            name="watch",
            argv=[str(self.python), "-m", "frappe.utils.bench_helper", "frappe", "watch"],
            log_file=self.bench.logs_path / "watch.log",
            working_dir=self.bench.sites_path,
            critical=False,
        )

    def worker_pool_definition(self, queues: str, num_workers: int) -> ProcessDefinition:
        return ProcessDefinition(
            name="worker_pool",
            argv=[
                str(self.python),
                "-m",
                "frappe.utils.bench_helper",
                "frappe",
                "worker-pool",
                "--num-workers",
                str(num_workers),
                "--queue",
                queues,
            ],
            log_file=self.bench.logs_path / "worker_pool.log",
            env=self.py_memory_env(),
            working_dir=self.bench.sites_path,
        )

    def worker_definitions(self, queue: str, count: int) -> list[ProcessDefinition]:
        sites = self.bench.sites_path
        # Commas in queue names break supervisor's programs= list; slug them.
        slug = re.sub(r"[^A-Za-z0-9]+", "_", queue).strip("_") or "default"
        return [
            ProcessDefinition(
                name=f"worker_{slug}_{i}",
                argv=[
                    str(self.python),
                    "-m",
                    "frappe.utils.bench_helper",
                    "frappe",
                    "worker",
                    "--queue",
                    queue,
                ],
                log_file=self.bench.logs_path / f"worker_{slug}_{i}.log",
                env=self.py_memory_env(),
                working_dir=sites,
            )
            for i in range(1, count + 1)
        ]

    def redis_definition(self, name: str, config_filename: str) -> ProcessDefinition:
        from pilot.managers.redis import redis_server_binary

        return ProcessDefinition(
            name=name,
            argv=[
                redis_server_binary() or "redis-server",
                f"{self.bench.config_path}/{config_filename}",
            ],
            log_file=self.bench.logs_path / f"{name}.log",
            stop_timeout=300,
        )

    def admin_definition(self) -> ProcessDefinition:
        root = cli_root()
        admin = AdminEnvManager(root)
        return ProcessDefinition(
            name="admin",
            argv=[
                str(admin.gunicorn),
                "-c",
                str(self.bench.config_path / "admin-gunicorn.conf.py"),
                "admin.backend.wsgi:application",
            ],
            log_file=self.bench.logs_path / "admin.log",
            env={
                "BENCH_ADMIN_ROOT": str(self.bench.path),
                "PYTHONPATH": str(root),
                "MALLOC_ARENA_MAX": "2",
            },
            working_dir=root,
        )

    def watch_admin_definition(self) -> ProcessDefinition:
        return self.build_admin_definition("--dev")

    def build_admin_definition(self, mode_flag: str) -> ProcessDefinition:
        root = cli_root()
        python = AdminEnvManager(root).python
        cfg = self.bench.config.admin
        return ProcessDefinition(
            name="admin",
            argv=[
                str(python),
                "-m",
                "admin.backend.run_server",
                "--bench-root",
                str(self.bench.path),
                "--port",
                str(cfg.port),
                "--timeout",
                str(cfg.timeout),
                mode_flag,
            ],
            log_file=self.bench.logs_path / "admin.log",
            env={"PYTHONPATH": str(root)},
        )

    def admin_frontend_dev_definition(self) -> ProcessDefinition:
        frontend_dir = cli_root() / "admin" / "frontend"
        cfg = self.bench.config.admin
        return ProcessDefinition(
            name="admin-ui",
            argv=["yarn", "--cwd", str(frontend_dir), "dev"],
            log_file=self.bench.logs_path / "admin-ui.log",
            env={"VITE_ADMIN_PORT": str(cfg.port)},
        )
