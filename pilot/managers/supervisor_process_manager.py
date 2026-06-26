from __future__ import annotations

import os
import subprocess
from pathlib import Path

from pilot.managers.admin_env_manager import AdminEnvManager
from pilot.managers.process_manager import ProcessDefinition, ProcessManager, _cli_root
from pilot.utils import run_command


class SupervisorProcessManager(ProcessManager):
    """Manages bench processes via a bench-owned supervisord instance (no sudo required)."""

    @property
    def supervisor_dir(self) -> Path:
        return self.bench.config_path / "supervisor"

    @property
    def supervisor_conf_path(self) -> Path:
        return self.supervisor_dir / "supervisord.conf"

    @property
    def supervisor_sock(self) -> Path:
        return self.supervisor_dir / "supervisord.sock"

    @property
    def supervisor_pid(self) -> Path:
        return self.supervisor_dir / "supervisord.pid"

    def _supervisorctl(self) -> list[str]:
        return ["supervisorctl", "-c", str(self.supervisor_conf_path)]

    def generate_config(self) -> None:
        AdminEnvManager(_cli_root()).ensure()
        self._ensure_redis_config()
        self._ensure_gunicorn_config()
        self.supervisor_dir.mkdir(parents=True, exist_ok=True)
        self.supervisor_conf_path.write_text(self._render_supervisord_conf())

    def install_config(self) -> None:
        self.supervisor_dir.mkdir(parents=True, exist_ok=True)

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

    @property
    def workload_group(self) -> str:
        return self.bench.config.name

    @property
    def admin_group(self) -> str:
        return f"{self.bench.config.name}-admin"

    def reload(self) -> None:
        if self.is_alive():
            run_command([*self._supervisorctl(), "reread"])
            run_command([*self._supervisorctl(), "update"])

    def start(self) -> None:
        self.generate_config()
        if self.is_alive():
            run_command([*self._supervisorctl(), "reread"])
            run_command([*self._supervisorctl(), "update"])
        else:
            run_command(["supervisord", "-c", str(self.supervisor_conf_path)])
        run_command([*self._supervisorctl(), "start", f"{self.admin_group}:*"])
        run_command([*self._supervisorctl(), "start", f"{self.workload_group}:*"])

    def setup_admin(self) -> None:
        """Bring up just the admin group, leaving the workload down — serves a new
        bench's setup wizard at its domain before it's initialized."""
        self.generate_config()
        if self.is_alive():
            run_command([*self._supervisorctl(), "reread"])
            run_command([*self._supervisorctl(), "update"])
        else:
            run_command(["supervisord", "-c", str(self.supervisor_conf_path)])
        run_command([*self._supervisorctl(), "start", f"{self.admin_group}:*"])

    def stop(self) -> None:
        """Stop the workload only; the admin group and supervisord daemon keep
        running so the control plane stays reachable while the workload is down."""
        if self.is_alive():
            run_command([*self._supervisorctl(), "stop", f"{self.workload_group}:*"])

    def stop_admin(self) -> None:
        """Stop the admin group; `bench start` brings it back. The supervisord
        daemon stays so the workload/admin can be restarted without a respawn."""
        if self.is_alive():
            run_command([*self._supervisorctl(), "stop", f"{self.admin_group}:*"])

    def shutdown(self) -> None:
        """Tear down everything, including the admin group and the daemon."""
        if self.is_alive():
            run_command([*self._supervisorctl(), "shutdown"])

    def restart(self) -> None:
        run_command([*self._supervisorctl(), "restart", f"{self.workload_group}:*"])

    def restart_admin(self) -> None:
        """Restart the admin group if supervisord is up, so on-disk code/asset
        changes (e.g. a rebuilt UI) take effect immediately."""
        if self.is_alive():
            run_command([*self._supervisorctl(), "restart", f"{self.admin_group}:*"])

    def is_running(self) -> bool:
        if not self.is_configured() or not self.is_alive():
            return False
        result = subprocess.run(
            [*self._supervisorctl(), "status", f"{self.workload_group}:*"],
            capture_output=True,
            text=True,
        )
        return "RUNNING" in result.stdout

    def admin_is_running(self) -> bool:
        if not self.is_configured() or not self.is_alive():
            return False
        result = subprocess.run(
            [*self._supervisorctl(), "status", f"{self.admin_group}:*"],
            capture_output=True,
            text=True,
        )
        return "RUNNING" in result.stdout

    def reload_workers(self, web_only: bool = False) -> None:
        cache_port = self.bench.config.redis.cache_port
        subprocess.run(["redis-cli", "-p", str(cache_port), "del", "assets_json"], capture_output=True)
        if self.is_running():
            if web_only:
                run_command([*self._supervisorctl(), "restart", f"{self.bench.config.name}:{self.bench.config.name}-web"])
            else:
                run_command([*self._supervisorctl(), "restart", f"{self.workload_group}:*"])

    def _render_supervisord_conf(self) -> str:
        name = self.bench.config.name
        defs = self._prod_process_definitions()
        workload = [pd for pd in defs if pd.name != "admin"]
        admin = [pd for pd in defs if pd.name == "admin"]

        def _names(items: list) -> str:
            return ",".join(f"{name}-{pd.name.replace('_', '-')}" for pd in items)

        sections: list[str] = [
            "[unix_http_server]",
            f"file={self.supervisor_sock}",
            "chmod=0700",
            "",
            "[supervisord]",
            f"logfile={self.bench.logs_path}/supervisord.log",
            "logfile_maxbytes=50MB",
            "logfile_backups=10",
            "loglevel=info",
            f"pidfile={self.supervisor_pid}",
            "nodaemon=false",
            "",
            "[rpcinterface:supervisor]",
            "supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface",
            "",
            "[supervisorctl]",
            f"serverurl=unix://{self.supervisor_sock}",
            "",
            # Workload and admin are separate groups so `bench stop` can stop the
            # workload while the admin control plane keeps running.
            f"[group:{self.workload_group}]",
            f"programs={_names(workload)}",
            "",
        ]
        if admin:
            sections += [
                f"[group:{self.admin_group}]",
                f"programs={_names(admin)}",
                "",
            ]
        for pd in defs:
            sections.append(self._render_program(pd, pd.name.replace("_", "-")))

        return "\n".join(sections)

    def _render_program(self, pd: ProcessDefinition, safe_name: str) -> str:
        import re

        log_dir = self.bench.logs_path
        cmd = pd.command

        env_vars: list[str] = []
        while True:
            m = re.match(r"^([A-Z_][A-Z0-9_]*)=(\S+)\s+", cmd)
            if not m:
                break
            env_vars.append(f'{m.group(1)}="{m.group(2)}"')
            cmd = cmd[m.end() :]
        for key, value in pd.env.items():
            env_vars.append(f'{key}="{value}"')

        directory = ""
        m2 = re.match(r"^cd\s+(\S+)\s*&&\s*", cmd)
        if m2:
            directory = m2.group(1)
            cmd = cmd[m2.end() :]

        lines = [
            f"[program:{self.bench.config.name}-{safe_name}]",
            f"command={cmd}",
            "autostart=true",
            "autorestart=true",
            "startretries=3",
            f"stdout_logfile={log_dir}/{pd.name}.log",
            f"stderr_logfile={log_dir}/{pd.name}.error.log",
            "stopasgroup=true",
            "killasgroup=true",
        ]
        if directory:
            lines.insert(2, f"directory={directory}")
        if env_vars:
            lines.insert(2, f"environment={','.join(env_vars)}")
        if pd.name == "web" and self.bench.config.production.use_companion_manager:
            lines.append("stopwaitsecs=1600")
        return "\n".join(lines) + "\n"
