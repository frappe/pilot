from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from bench_cli.managers.admin_env_manager import AdminEnvManager
from bench_cli.managers.process_manager import ProcessDefinition, ProcessManager, _cli_root
from bench_cli.utils import run_command


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
        self.supervisor_dir.mkdir(parents=True, exist_ok=True)
        self.supervisor_conf_path.write_text(self._render_supervisord_conf())

    def install_config(self) -> None:
        self.supervisor_dir.mkdir(parents=True, exist_ok=True)

    def is_configured(self) -> bool:
        return self.supervisor_conf_path.exists()

    def _is_supervisord_alive(self) -> bool:
        if not self.supervisor_pid.exists():
            return False
        try:
            pid = int(self.supervisor_pid.read_text().strip())
            os.kill(pid, 0)
            return True
        except (ValueError, ProcessLookupError, OSError):
            print("HERE!")
            return False

    def reload(self) -> None:
        if self._is_supervisord_alive():
            run_command([*self._supervisorctl(), "reread"])
            run_command([*self._supervisorctl(), "update"])

    def start(self) -> None:
        self.generate_config()
        if self._is_supervisord_alive():
            run_command([*self._supervisorctl(), "reread"])
            run_command([*self._supervisorctl(), "update"])
        else:
            run_command(["supervisord", "-c", str(self.supervisor_conf_path)])
        run_command([*self._supervisorctl(), "start", f"{self.bench.config.name}:*"])

    def stop(self) -> None:
        if self._is_supervisord_alive():
            run_command([*self._supervisorctl(), "shutdown"])

    def restart(self) -> None:
        run_command([*self._supervisorctl(), "restart", f"{self.bench.config.name}:*"])

    def is_running(self) -> bool:
        if not self._is_supervisord_alive():
            return False
        result = subprocess.run(
            [*self._supervisorctl(), "status", f"{self.bench.config.name}:*"],
            capture_output=True,
            text=True,
        )
        return "RUNNING" in result.stdout

    def reload_web(self) -> None:
        cache_port = self.bench.config.redis.cache_port
        subprocess.run(["redis-cli", "-p", str(cache_port), "del", "assets_json"], capture_output=True)
        if self.is_running():
            print("Restarting web worker to pick up new assets...")
            run_command([*self._supervisorctl(), "restart", f"{self.bench.config.name}:{self.bench.config.name}-web"])

    def _render_supervisord_conf(self) -> str:
        defs = self._prod_process_definitions()
        program_names = ",".join(
            f"{self.bench.config.name}-{pd.name.replace('_', '-')}" for pd in defs
        )
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
            f"[group:{self.bench.config.name}]",
            f"programs={program_names}",
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
            cmd = cmd[m.end():]

        directory = ""
        m2 = re.match(r"^cd\s+(\S+)\s*&&\s*", cmd)
        if m2:
            directory = m2.group(1)
            cmd = cmd[m2.end():]

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
        return "\n".join(lines) + "\n"
