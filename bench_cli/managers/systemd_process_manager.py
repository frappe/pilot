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
        self._ensure_gunicorn_config()
        self.systemd_conf_dir.mkdir(parents=True, exist_ok=True)

        # Remove stale unit files (e.g. after switching process managers or enabling
        # companion mode, which drops socketio/worker services).
        target_file = self._target_name()
        for path in list(self.systemd_conf_dir.iterdir()):
            if path.is_file() and (path.suffix == ".service" or path.name == target_file):
                path.unlink()

        defs = self._prod_process_definitions()
        for pd in defs:
            (self.systemd_conf_dir / self._unit_name(pd.name)).write_text(self._render_unit(pd))
        (self.systemd_conf_dir / self._target_name()).write_text(self._render_target(defs))

    def install_config(self) -> None:
        import getpass

        self.user_unit_dir.mkdir(parents=True, exist_ok=True)
        defs = self._prod_process_definitions()
        units = set(self._unit_name(pd.name) for pd in defs) | {self._target_name()}

        # Remove stale user-unit symlinks pointing to this bench's config dir.
        for dst in self.user_unit_dir.iterdir():
            if not (dst.is_symlink() and dst.exists()):
                continue
            try:
                points_to_bench = dst.resolve().parent == self.systemd_conf_dir.resolve()
            except OSError:
                continue
            if points_to_bench and dst.name not in units:
                dst.unlink()

        for unit in units:
            src = (self.systemd_conf_dir / unit).resolve()
            dst = self.user_unit_dir / unit
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            dst.symlink_to(src)

        subprocess.run(
            ["sudo", "loginctl", "enable-linger", getpass.getuser()],
            capture_output=True,
            check=False,
        )

        subprocess.run(
            ["sudo", "systemctl", "start", f"user@{os.getuid()}.service"],
            capture_output=True,
            check=False,
        )

        env = self._systemctl_env()
        run_command(self._systemctl("daemon-reload"), env=env)
        run_command(self._systemctl("enable", self._target_name()), env=env)

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
        try:
            result = subprocess.run(
                self._systemctl("is-active", self._target_name()),
                capture_output=True,
                env=self._systemctl_env(),
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False

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
        if pd.name == "web" and self.bench.config.production.use_companion_manager:
            lines.append("TimeoutStopSec=1600")
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
