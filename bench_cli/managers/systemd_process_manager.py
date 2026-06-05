from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from bench_cli.managers.process_manager import ProcessDefinition, ProcessManager, _cli_root
from bench_cli.utils import run_command


class SystemdProcessManager(ProcessManager):
    """Manages bench processes via systemd --user (no sudo required)."""

    @property
    def systemd_conf_dir(self) -> Path:
        return self.bench.config_path / "systemd"

    @property
    def user_unit_dir(self) -> Path:
        return Path.home() / ".config" / "systemd" / "user"

    def _unit_name(self, service_name: str) -> str:
        return f"{self.bench.config.name}-{service_name}.service"

    def _target_name(self) -> str:
        return f"{self.bench.config.name}.target"

    def _systemctl_env(self) -> dict:
        env = dict(os.environ)
        runtime_dir = env.get("XDG_RUNTIME_DIR")

        if not runtime_dir:
            env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"

        return env

    def _systemctl(self, *args: str) -> list[str]:
        return ["systemctl", "--user", *args]

    def generate_config(self) -> None:
        from bench_cli.managers.admin_env_manager import AdminEnvManager

        AdminEnvManager(_cli_root()).ensure()
        self.systemd_conf_dir.mkdir(parents=True, exist_ok=True)
        defs = self._prod_process_definitions()
        for pd in defs:
            (self.systemd_conf_dir / self._unit_name(pd.name)).write_text(self._render_unit(pd))
        (self.systemd_conf_dir / self._target_name()).write_text(self._render_target(defs))

    def install_config(self) -> None:
        self.user_unit_dir.mkdir(parents=True, exist_ok=True)
        defs = self._prod_process_definitions()
        units = [self._unit_name(pd.name) for pd in defs] + [self._target_name()]
        for unit in units:
            src = (self.systemd_conf_dir / unit).resolve()
            dst = self.user_unit_dir / unit
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            dst.symlink_to(src)
        env = self._systemctl_env()
        try:
            run_command(self._systemctl("daemon-reload"), env=env)
            run_command(self._systemctl("enable", self._target_name()), env=env)
        except Exception:
            print(
                f"\nNote: Could not reach the user systemd bus for {os.environ.get('USER', 'this user')}.\n"
                "If linger is not yet enabled, run once as root:\n"
                f"  sudo loginctl enable-linger {os.environ.get('USER', '<bench-user>')}\n"
                "Then re-run: bench setup production"
            )

    def is_configured(self) -> bool:
        result = subprocess.run(
            self._systemctl("is-enabled", self._target_name()),
            capture_output=True,
            env=self._systemctl_env(),
        )
        return result.returncode == 0

    def reload(self) -> None:
        run_command(self._systemctl("daemon-reload"), env=self._systemctl_env())

    def start(self) -> None:
        self.generate_config()
        self.reload()
        run_command(self._systemctl("start", self._target_name()), env=self._systemctl_env())

    def stop(self) -> None:
        run_command(self._systemctl("stop", self._target_name()), env=self._systemctl_env())

    def restart(self) -> None:
        run_command(self._systemctl("restart", self._target_name()), env=self._systemctl_env())

    def is_running(self) -> bool:
        result = subprocess.run(
            self._systemctl("is-active", self._target_name()),
            capture_output=True,
            env=self._systemctl_env(),
        )
        return result.returncode == 0

    def reload_web(self) -> None:
        cache_port = self.bench.config.redis.cache_port
        subprocess.run(
            ["redis-cli", "-p", str(cache_port), "del", "assets_json"],
            capture_output=True,
        )
        if self.is_running():
            print("Restarting web worker to pick up new assets...")
            run_command(
                self._systemctl("restart", self._unit_name("web")),
                env=self._systemctl_env(),
            )

    def _render_unit(self, pd: ProcessDefinition) -> str:
        cmd = pd.command

        env_lines: list[str] = []
        while m := re.match(r"^([A-Z_][A-Z0-9_]*)=(\S+)\s+", cmd):
            env_lines.append(f"Environment={m.group(1)}={m.group(2)}")
            cmd = cmd[m.end() :]

        working_dir = ""
        if m2 := re.match(r"^cd\s+(\S+)\s*&&\s*", cmd):
            working_dir = m2.group(1)
            cmd = cmd[m2.end() :]

        is_redis = cmd.startswith("redis-server")
        lines = [
            "[Unit]",
            f"Description={self.bench.config.name} {pd.name}",
            f"PartOf={self._target_name()}",
            "",
            "[Service]",
            "Type=simple",
        ]
        if working_dir:
            lines.append(f"WorkingDirectory={working_dir}")
        lines += env_lines
        lines += [
            f"ExecStart={cmd}",
            "Restart=on-failure",
        ]
        if is_redis:
            lines.append("TimeoutStopSec=300")
        lines += [
            f"StandardOutput=append:{pd.log_file}",
            f"StandardError=append:{pd.log_file}.error.log",
        ]
        return "\n".join(lines) + "\n"

    def _render_target(self, defs: list[ProcessDefinition]) -> str:
        wants = " ".join(self._unit_name(pd.name) for pd in defs)
        return (
            "\n".join(
                [
                    "[Unit]",
                    f"Description={self.bench.config.name} bench",
                    f"Wants={wants}",
                    "",
                    "[Install]",
                    "WantedBy=default.target",
                ]
            )
            + "\n"
        )
