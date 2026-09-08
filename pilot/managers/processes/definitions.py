from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.managers.environment import AdminEnvManager
from pilot.utils import cli_root

if TYPE_CHECKING:
    from pilot.core.bench import Bench

CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass
class ProcessDefinition:
    name: str
    argv: list[str]  # executable + args - no shell, no `cd`, no inline env prefix
    log_file: Path
    env: dict = field(default_factory=dict)
    working_dir: Path | None = None  # was `cd {dir} &&`
    stop_timeout: int | None = None  # graceful-stop seconds (redis=300)
    critical: bool = True  # dev runner stops the whole bench when this process exits
    restart_on_failure: bool = True
    pre_run: list[str] = field(default_factory=list)  # argv run before the process starts
    post_run: list[str] = field(default_factory=list)  # argv run after it stops


def hook_wrapped_argv(pd: ProcessDefinition) -> list[str]:
    """argv for managers with no native pre/post hooks (supervisor, dev runner).

    Supervisor and the dev runner send SIGTERM to the whole process group. An
    EXIT-only trap does not reliably fire when `sh` itself is killed by that
    signal while blocked waiting on the foreground child - only an explicit
    TERM trap runs before the shell's default disposition takes it down. So
    TERM is trapped to forward the signal to the child, wait for it, then run
    post_run and exit; EXIT is also trapped for the self-exit case (post_run
    or pre_run itself failing)."""
    if not pd.pre_run and not pd.post_run:
        return pd.argv
    script = shlex.join(pd.argv)
    if pd.pre_run:
        script = f"{shlex.join(pd.pre_run)} && {script}"
    if pd.post_run:
        # Defined as a function rather than inlined into the trap string, since
        # post_run's own argv is already shlex-quoted and a second layer of
        # quoting around it (single or double) breaks on any quote it contains.
        # _post_run runs at most once, guarded by _ran: a TERM trap fires it and
        # forwards the signal to the backgrounded child, then the shell's own
        # exit re-fires the EXIT trap - which the guard turns into a no-op.
        post = shlex.join(pd.post_run)
        script = (
            f"_ran=0\n"
            f"_post_run() {{ [ \"$_ran\" = 1 ] && return; _ran=1; {post}; }}\n"
            f"trap _post_run EXIT\n"
            f'trap "kill \\$child 2>/dev/null" TERM\n'
            f"{script} &\n"
            f"child=$!\n"
            f"wait $child"
        )
    return ["sh", "-c", script]


def reject_control_chars(pd: ProcessDefinition) -> None:
    """A control character in a declared field would inject directives into a unit file."""
    fields = [pd.name, str(pd.working_dir or ""), *pd.argv, *pd.pre_run, *pd.post_run]
    for key, value in pd.env.items():
        fields += [key, value]
    if any(CONTROL_RE.search(field) for field in fields):
        raise ValueError(f"process '{pd.name}' has a control character in a unit field")


class ProcessDefinitionBuilder:
    def __init__(self, bench: "Bench", python: Path, watch_admin_js: bool) -> None:
        self.bench = bench
        self.python = python
        self.watch_admin_js = watch_admin_js

    def prod_process_definitions(self) -> list[ProcessDefinition]:
        if self.bench.is_lite_mode:
            # web, realtime and jobs all live in the one lite process.
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
        defs.extend(self.app_process_definitions())
        self._reject_name_collisions(defs)
        return defs

    def _reject_name_collisions(self, defs: list[ProcessDefinition]) -> None:
        """Supervisor normalizes underscores to dashes in program names, so two
        distinct pd.name values can render to the same unit/program name."""
        seen: dict[str, str] = {}
        for pd in defs:
            normalized = pd.name.replace("_", "-")
            if normalized in seen and seen[normalized] != pd.name:
                raise ValueError(
                    f"process name '{pd.name}' collides with '{seen[normalized]}' "
                    f"once normalized to '{normalized}'"
                )
            seen[normalized] = pd.name

    def app_process_definitions(self) -> list[ProcessDefinition]:
        """A malformed declaration raises rather than being skipped - a skipped app
        reads as "removed" to reconciliation, which would stop its live services.

        An operator can still opt an app out via bench.toml's disabled_app_processes,
        the escape hatch for a third-party app whose declaration can't be fixed
        in time - that skip is explicit, not automatic."""
        disabled = set(self.bench.config.disabled_app_processes)
        defs: list[ProcessDefinition] = []
        for app in self.bench.apps():
            if app.config.name in disabled:
                continue
            defs.extend(app.requirements.process_definitions())
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

    def python_env(self) -> dict:
        """Shared env for Python processes. PYTHONUNBUFFERED keeps stdout unbuffered:
        every runner captures it into a pipe or log file, where Python would otherwise
        block-buffer app-code print() until ~8KB accumulates."""
        return {"PYTHONUNBUFFERED": "1"}

    def web_definition(self, dev: bool = False) -> ProcessDefinition:
        sites = self.bench.sites_path
        if self.bench.is_lite_mode:
            return self.lite_definition(dev=dev)
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
                env={**self.python_env(), "DEV_SERVER": "1"},
                working_dir=sites,
            )
        gunicorn = self.bench.env_path / "bin" / "gunicorn"
        return ProcessDefinition(
            name="web",
            argv=[str(gunicorn), "-c", "../config/gunicorn.conf.py", "frappe.app:application"],
            log_file=self.bench.logs_path / "web.log",
            env=self.python_env(),
            working_dir=sites,
        )

    def lite_definition(self, dev: bool = False) -> ProcessDefinition:
        """Web, realtime and background jobs in one self-recycling process."""
        lite_mode = self.bench.config.lite_mode
        argv = [
            str(self.python),
            "-m",
            "frappe.runner",
            "--host=127.0.0.1",
            f"--port={self.bench.config.http_port}",
            f"--queue={','.join(self.bench.config.workers.queues)}",
            f"--job-threads={self.bench.config.workers.count}",
            f"--restart-after-requests={lite_mode.restart_after_requests}",
            f"--restart-after-jobs={lite_mode.restart_after_jobs}",
            f"--restart-idle-seconds={lite_mode.restart_idle_seconds}",
            f"--request-drain-seconds={lite_mode.request_drain_seconds}",
            f"--job-drain-seconds={lite_mode.job_drain_seconds}",
        ]
        if dev:
            argv.append("--dev")
            argv.append("--verbose")
        return ProcessDefinition(
            name="web",
            argv=argv,
            log_file=self.bench.logs_path / "web.log",
            # One threaded process: capping the glibc arenas keeps the heap from
            # fragmenting across them, and preloading only pays off before a fork.
            env={**self.python_env(), "MALLOC_ARENA_MAX": "2", "FRAPPE_PRELOAD_MODULES": "0"},
            working_dir=self.bench.sites_path,
            stop_timeout=lite_mode.stop_timeout,
        )

    def socketio_definition(self) -> ProcessDefinition:
        if self.bench.config.socketio_backend == "python":
            argv = [str(self.python), "-m", "frappe.realtime.server"]
            working_dir = self.bench.path
            backend_env = self.python_env()
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
            env=self.python_env(),
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
            env=self.python_env(),
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
                env=self.python_env(),
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
                **self.python_env(),
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
            env={**self.python_env(), "PYTHONPATH": str(root)},
        )

    def admin_frontend_dev_definition(self) -> ProcessDefinition:
        frontend_dir = cli_root() / "admin" / "frontend"
        cfg = self.bench.config.admin
        return ProcessDefinition(
            name="admin-ui",
            argv=["npm", "--prefix", str(frontend_dir), "run", "dev"],
            log_file=self.bench.logs_path / "admin-ui.log",
            env={"VITE_ADMIN_PORT": str(cfg.port)},
        )
