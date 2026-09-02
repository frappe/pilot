from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from pilot.managers.environment import AdminEnvManager
from pilot.managers.gunicorn import GunicornManager
from pilot.managers.processes.base import (
    ManagedProcessManager,
    ServiceRenderer,
    UnitGroup,
    override,
)
from pilot.managers.processes.definitions import hook_wrapped_argv, reject_control_chars
from pilot.managers.processes.local import ProcessDefinition
from pilot.utils import cli_root, run_command


# supervisorctl status states -> the status vocabulary the admin UI shows.
_SUPERVISOR_STATE_TO_STATUS = {
    "RUNNING": "running",
    "STARTING": "starting",
    "STOPPING": "stopping",
    "STOPPED": "stopped",
    "EXITED": "stopped",
    "FATAL": "start_failed",
    "BACKOFF": "start_failed",
    "UNKNOWN": "unknown",
}


def _escape_percent(value: str) -> str:
    # supervisord expands %(...)s in every value, quoted or not.
    return value.replace("%", "%%")


def _escape_quoted(value: str) -> str:
    # Only for a value that will itself be wrapped in "...": also escape its quotes.
    return _escape_percent(value).replace('"', '\\"')


class SupervisorRenderer(ServiceRenderer):
    """Builds the bench's supervisord.conf and per-program blocks."""

    def __init__(self, bench_name: str, log_dir) -> None:
        super().__init__(bench_name)
        self.log_dir = log_dir

    @override
    def render(self, pd: ProcessDefinition) -> str:
        reject_control_chars(pd)
        directory = f"directory={pd.working_dir}\n" if pd.working_dir else ""
        env = ""
        if pd.env:
            pairs = ",".join(f'{k}="{_escape_quoted(v)}"' for k, v in pd.env.items())
            env = f"environment={pairs}\n"
        stop = f"stopwaitsecs={pd.stop_timeout}\n" if pd.stop_timeout is not None else ""
        return (
            f"[program:{self.get_program_name(pd)}]\n"
            f"command={_escape_percent(shlex.join(hook_wrapped_argv(pd)))}\n"
            f"{env}{directory}"
            f"autostart=true\n"
            # 'unexpected' restarts only on a non-zero exit, matching systemd's
            # Restart=on-failure; 'true' would also restart a clean, expected exit.
            f"autorestart={'unexpected' if pd.restart_on_failure else 'false'}\n"
            f"exitcodes=0\n"
            f"startretries=3\n"
            f"stdout_logfile={self.log_dir}/{pd.name}.log\n"
            f"stderr_logfile={self.log_dir}/{pd.name}.error.log\n"
            f"stopasgroup=true\n"
            f"killasgroup=true\n"
            f"{stop}"
        )

    def render_supervisord_conf(self, defs: list[ProcessDefinition], sock, pid) -> str:
        workload = [pd for pd in defs if pd.name != "admin"]
        admin = [pd for pd in defs if pd.name == "admin"]
        admin_group = f"[group:{self.bench_name}-admin]\nprograms={self._csv(admin)}\n\n" if admin else ""
        programs = "\n".join(self.render(pd) for pd in defs)
        return (
            f"[unix_http_server]\n"
            f"file={sock}\n"
            f"chmod=0700\n\n"
            f"[supervisord]\n"
            f"logfile={self.log_dir}/supervisord.log\n"
            f"logfile_maxbytes=50MB\n"
            f"logfile_backups=10\n"
            f"loglevel=info\n"
            f"pidfile={pid}\n"
            f"nodaemon=false\n\n"
            f"[rpcinterface:supervisor]\n"
            f"supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface\n\n"
            f"[supervisorctl]\n"
            f"serverurl=unix://{sock}\n\n"
            f"[group:{self.bench_name}]\n"
            f"programs={self._csv(workload)}\n\n"
            f"{admin_group}"
            f"{programs}"
        )

    def get_program_name(self, pd: ProcessDefinition) -> str:
        return f"{self.bench_name}-{pd.name.replace('_', '-')}"

    def _csv(self, items: list[ProcessDefinition]) -> str:
        return ",".join(self.get_program_name(pd) for pd in items)


class SupervisorProcessManager(ManagedProcessManager):
    """Manages bench processes via a bench-owned supervisord instance (no sudo required)."""

    @property
    def supervisor_dir(self) -> Path:
        return self.bench.config_path / "services"

    @property
    def supervisor_conf_path(self) -> Path:
        return self.supervisor_dir / "supervisord.conf"

    @property
    def supervisor_sock(self) -> Path:
        return self.supervisor_dir / "supervisord.sock"

    @property
    def supervisor_pid(self) -> Path:
        return self.supervisor_dir / "supervisord.pid"

    @property
    def workload_group(self) -> str:
        return self.bench.config.name

    @property
    def admin_group(self) -> str:
        return f"{self.bench.config.name}-admin"

    @override
    def write_config(self) -> None:
        AdminEnvManager(cli_root()).ensure()
        self._ensure_redis_config()
        self._ensure_gunicorn_config()
        GunicornManager(self.bench).generate_admin_config()
        self.supervisor_dir.mkdir(parents=True, exist_ok=True)
        renderer = SupervisorRenderer(self.bench.config.name, self.bench.logs_path)
        self.supervisor_conf_path.write_text(
            renderer.render_supervisord_conf(self._prod_process_definitions(), self.supervisor_sock, self.supervisor_pid)
        )

    @override
    def install_config(self) -> None:
        self.supervisor_dir.mkdir(parents=True, exist_ok=True)

    @override
    def reload_manager_config(self) -> None:
        if self.is_alive():
            run_command([*self._supervisorctl(), "reread"])
            run_command([*self._supervisorctl(), "update"])

    @override
    def ensure_ready(self) -> None:
        if self.is_alive():
            run_command([*self._supervisorctl(), "reread"])
            run_command([*self._supervisorctl(), "update"])
        else:
            run_command(["supervisord", "-c", str(self.supervisor_conf_path)])

    @override
    def apply_unit_action(self, action: str, role: UnitGroup) -> None:
        # stop/admin-restart are no-ops when the daemon is down to avoid supervisorctl errors.
        if (action == "stop" or (action == "restart" and role is UnitGroup.ADMIN)) and not self.is_alive():
            return
        run_command([*self._supervisorctl(), action, self._target(role)])

    @override
    def are_units_running(self, role: UnitGroup) -> bool:
        if not self.is_configured() or not self.is_alive():
            return False
        group = self.admin_group if role is UnitGroup.ADMIN else self.workload_group
        result = subprocess.run(
            [*self._supervisorctl(), "status", f"{group}:*"],
            capture_output=True,
            text=True,
        )
        return "RUNNING" in result.stdout

    def is_configured(self) -> bool:
        return self.supervisor_conf_path.exists()

    def is_alive(self) -> bool:
        if not self.supervisor_pid.exists():
            return False
        try:
            pid = int(self.supervisor_pid.read_text().strip())
            os.kill(pid, 0)
            return True
        except (ValueError, ProcessLookupError, OSError):
            return False

    def shutdown(self) -> None:
        """Tear down everything, including the admin group and the daemon."""
        if self.is_alive():
            run_command([*self._supervisorctl(), "shutdown"])

    def _supervisorctl(self) -> list[str]:
        return ["supervisorctl", "-c", str(self.supervisor_conf_path)]

    def _target(self, role: UnitGroup) -> str:
        if role is UnitGroup.ADMIN:
            return f"{self.admin_group}:*"
        if role is UnitGroup.WEB:
            return f"{self.bench.config.name}:{self.bench.config.name}-web"
        return f"{self.workload_group}:*"

    def apply_process_action(self, action: str, process_name: str) -> None:
        """start/stop/restart one declared process by its ProcessDefinition name."""
        program = f"{self.bench.config.name}-{process_name.replace('_', '-')}"
        run_command([*self._supervisorctl(), action, f"{self.workload_group}:{program}"])

    def live_states(self) -> dict[str, dict]:
        """{process_name: {status, pid}} for every workload program this bench owns."""
        if not self.is_configured() or not self.is_alive():
            return {}
        result = subprocess.run(
            [*self._supervisorctl(), "status", f"{self.workload_group}:*"],
            capture_output=True,
            text=True,
        )
        states: dict[str, dict] = {}
        prefix = f"{self.bench.config.name}-"
        for line in result.stdout.splitlines():
            parts = line.split()
            if not parts:
                continue
            program = parts[0].split(":", 1)[-1]  # "group:program" -> "program"
            state = parts[1] if len(parts) > 1 else "UNKNOWN"
            pid = None
            if state == "RUNNING" and len(parts) > 3 and parts[2] == "pid":
                pid = int(parts[3].rstrip(","))
            # Program names have underscores dashed; map back via a stripped key match later.
            states[program.removeprefix(prefix)] = {
                "status": _SUPERVISOR_STATE_TO_STATUS.get(state, "unknown"),
                "pid": pid,
            }
        return states
