from __future__ import annotations

import os
import subprocess
from pathlib import Path

from pilot.managers.admin_environment import AdminEnvManager
from pilot.managers.gunicorn import GunicornManager
from pilot.loader import cli_root
from pilot.managers.processes.local import ProcessDefinition
from pilot.managers.processes.base import (
    ManagedProcessManager,
    UnitGroup,
    override,
)
from pilot.managers.platform import _privileged
from pilot.managers.processes.systemd_render import SystemdRenderer
from pilot.utils import run_command

_ADMIN_IDLE_TIMEOUT = 60  # seconds of inactivity before socket-activated admin stops
_SYSTEMCTL_TIMEOUT = 90

class SystemdProcessManager(ManagedProcessManager):
    """Manages bench processes via systemd --user (no sudo required)."""

    @property
    def systemd_conf_dir(self) -> Path:
        return self.bench.config_path / "systemd"

    @property
    def user_unit_dir(self) -> Path:
        return Path.home() / ".config" / "systemd" / "user"

    @override
    def write_config(self) -> None:
        AdminEnvManager(cli_root()).ensure()
        self._ensure_redis_config()
        self._ensure_gunicorn_config()
        GunicornManager(self.bench).generate_admin_config()
        self.systemd_conf_dir.mkdir(parents=True, exist_ok=True)

        target_file = self._target_name()
        for path in list(self.systemd_conf_dir.iterdir()):
            if path.is_file() and (path.suffix in (".service", ".socket") or path.name == target_file):
                path.unlink()

        renderer = SystemdRenderer(self.bench.config.name)
        workload_units: list[str] = []
        for pd in self._prod_process_definitions():
            if pd.name == "admin":
                (self.systemd_conf_dir / self._unit_name("admin")).write_text(self._admin_service_text())
                (self.systemd_conf_dir / self._admin_socket_name()).write_text(renderer.admin_socket(self.bench.config.admin.internal_port))
            else:
                (self.systemd_conf_dir / self._unit_name(pd.name)).write_text(renderer.render(pd))
                workload_units.append(self._unit_name(pd.name))
        (self.systemd_conf_dir / self._target_name()).write_text(renderer.target(workload_units))

    @override
    def install_config(self) -> None:
        import getpass

        self.user_unit_dir.mkdir(parents=True, exist_ok=True)
        defs = self._prod_process_definitions()
        units = set(self._unit_name(pd.name) for pd in defs) | {self._target_name(), self._admin_socket_name()}

        # Stop dropped units so they release ports before relinking.
        self._reap_stale_units(units)

        for dst in self.user_unit_dir.iterdir():
            if not dst.is_symlink():
                continue
            try:
                points_to_bench = dst.resolve(strict=False).parent == self.systemd_conf_dir.resolve()
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
            _privileged(["loginctl", "enable-linger", getpass.getuser()]),
            capture_output=True,
            check=False,
        )
        subprocess.run(
            _privileged(["systemctl", "start", f"user@{os.getuid()}.service"]),
            capture_output=True,
            check=False,
        )

        env = self._systemctl_env()
        run_command(self._systemctl("daemon-reload"), env=env)
        # reset-failed clears rate-limit state so re-deploys can restart the admin socket.
        subprocess.run(self._systemctl("reset-failed", *units), capture_output=True, env=env)
        run_command(self._systemctl("enable", self._target_name()), env=env)
        self._activate_admin_socket(env)

    @override
    def reload_manager_config(self) -> None:
        run_command(self._systemctl("daemon-reload"), env=self._systemctl_env())

    @override
    def ensure_ready(self) -> None:
        self.reload_manager_config()

    @override
    def apply_unit_action(self, action: str, role: UnitGroup) -> None:
        env = self._systemctl_env()
        if role is UnitGroup.ADMIN:
            self._control_admin(action, env)
        elif role is UnitGroup.WEB:
            run_command(self._systemctl("restart", self._unit_name("web")), env=env)
        else:
            run_command(self._systemctl(action, self._target_name()), env=env)

    @override
    def are_units_running(self, role: UnitGroup) -> bool:
        env = self._systemctl_env()
        if role is UnitGroup.ADMIN:
            # A listening socket counts as reachable (socket-activated).
            for unit in (self._admin_socket_name(), self._unit_name("admin")):
                try:
                    result = subprocess.run(self._systemctl("is-active", unit), capture_output=True, env=env)
                except FileNotFoundError:
                    return False
                if result.returncode == 0:
                    return True
            return False
        try:
            result = subprocess.run(self._systemctl("is-active", self._target_name()), capture_output=True, env=env)
            return result.returncode == 0
        except FileNotFoundError:
            return False

    def start_admin(self) -> None:
        # Base start_admin only daemon-reloads; we need full install for a new bench.
        self.bench.logs_path.mkdir(parents=True, exist_ok=True)
        self.write_config()
        self.install_config()

    def is_configured(self) -> bool:
        result = subprocess.run(
            self._systemctl("is-enabled", self._target_name()),
            capture_output=True,
            env=self._systemctl_env(),
        )
        return result.returncode == 0

    def remove_units(self) -> None:
        """Stop, disable and unlink every unit this bench owns (best-effort)."""
        env = self._systemctl_env()
        units = self._installed_bench_units() | {self._target_name()}
        subprocess.run(self._systemctl("stop", self._target_name()), capture_output=True, env=env)
        for unit in units:
            subprocess.run(self._systemctl("stop", unit), capture_output=True, env=env)
            subprocess.run(self._systemctl("disable", unit), capture_output=True, env=env)
        if self.user_unit_dir.is_dir():
            for dst in list(self.user_unit_dir.iterdir()):
                if not dst.is_symlink():
                    continue
                try:
                    if dst.resolve(strict=False).parent == self.systemd_conf_dir.resolve():
                        dst.unlink()
                except OSError:
                    continue
        subprocess.run(self._systemctl("daemon-reload"), capture_output=True, env=env)

    def _unit_name(self, service_name: str) -> str:
        return f"{self.bench.config.name}-{service_name}.service"

    def _admin_socket_name(self) -> str:
        return f"{self.bench.config.name}-admin.socket"

    def _target_name(self) -> str:
        return f"{self.bench.config.name}.target"

    def _systemctl_env(self) -> dict:
        env = dict(os.environ)
        if not env.get("XDG_RUNTIME_DIR"):
            env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
        return env

    def _systemctl(self, *args: str) -> list[str]:
        return ["systemctl", "--user", *args]

    def _admin_service_text(self) -> str:
        root = cli_root()
        gunicorn = AdminEnvManager(root).gunicorn
        admin_conf = GunicornManager(self.bench).admin_config_path
        pd = ProcessDefinition(
            name="admin",
            argv=[str(gunicorn), "-c", str(admin_conf), "admin.backend.wsgi:application"],
            log_file=self.bench.logs_path / "admin.log",
            env={
                "BENCH_ADMIN_ROOT": str(self.bench.path),
                "PYTHONPATH": str(root),
                "BENCH_ADMIN_IDLE_TIMEOUT": str(_ADMIN_IDLE_TIMEOUT),
                "MALLOC_ARENA_MAX": "2",
            },
            working_dir=root,
        )
        return SystemdRenderer(self.bench.config.name).admin_service(pd, self._admin_socket_name())

    def _control_admin(self, action: str, env: dict) -> None:
        if action == "start":
            self._activate_admin_socket(env)
        elif action == "stop":
            for unit in (self._admin_socket_name(), self._unit_name("admin")):
                subprocess.run(self._systemctl("stop", unit), capture_output=True, env=env)
        elif action == "restart":
            service = self._unit_name("admin")
            if (self.user_unit_dir / service).exists():
                subprocess.run(self._systemctl("reset-failed", service), capture_output=True, env=env)
                run_command(self._systemctl("restart", service), env=env, timeout=_SYSTEMCTL_TIMEOUT)

    def _activate_admin_socket(self, env: dict) -> None:
        # Stop the service first: a stale port hold would make the new socket 502.
        socket = self._admin_socket_name()
        service = self._unit_name("admin")
        subprocess.run(self._systemctl("stop", service), capture_output=True, env=env)
        subprocess.run(self._systemctl("reset-failed", socket, service), capture_output=True, env=env)
        run_command(self._systemctl("enable", socket), env=env, timeout=_SYSTEMCTL_TIMEOUT)
        run_command(self._systemctl("restart", socket), env=env, timeout=_SYSTEMCTL_TIMEOUT)

    def _installed_bench_units(self) -> set[str]:
        result = subprocess.run(
            self._systemctl(
                "list-units",
                "--all",
                "--no-legend",
                "--plain",
                "--type=service,socket",
                f"{self.bench.config.name}-*",
            ),
            capture_output=True,
            text=True,
            env=self._systemctl_env(),
        )
        units = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if parts and (parts[0].endswith(".service") or parts[0].endswith(".socket")):
                units.add(parts[0])
        return units

    def _reap_stale_units(self, desired: set[str]) -> None:
        env = self._systemctl_env()
        for unit in self._installed_bench_units() - desired:
            subprocess.run(self._systemctl("stop", unit), capture_output=True, env=env)
            subprocess.run(self._systemctl("disable", unit), capture_output=True, env=env)
