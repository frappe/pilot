from __future__ import annotations

import getpass
import os
import subprocess
from pathlib import Path

from pilot.managers.environment import AdminEnvManager
from pilot.managers.gunicorn import GunicornManager
from pilot.managers.platform import _privileged
from pilot.managers.processes.base import (
    ManagedProcessManager,
    UnitGroup,
    override,
)
from pilot.managers.processes.local import ProcessDefinition
from pilot.managers.processes.systemd_render import SystemdRenderer
from pilot.managers.systemd_user import SystemdUserMixin
from pilot.utils import cli_root, run_command

_ADMIN_IDLE_TIMEOUT = 60  # seconds of inactivity before socket-activated admin stops
_SYSTEMCTL_TIMEOUT = 90

# systemd SubState -> the status vocabulary the admin UI shows.
_STATE_TO_STATUS = {
    "running": "running",
    "start-pre": "starting",
    "start": "starting",
    "start-post": "starting",
    "stop": "stopping",
    "stop-sigterm": "stopping",
    "stop-sigkill": "stopping",
    "stop-post": "stopping",
    "auto-restart": "restarting",
    "dead": "stopped",
    "exited": "stopped",
    "failed": "failed",
}


class SystemdProcessManager(SystemdUserMixin, ManagedProcessManager):
    """Manages bench processes via systemd --user (no sudo required)."""

    # Until write_config compares them, assume admin needs re-activation.
    admin_units_changed = True

    @property
    def systemd_conf_dir(self) -> Path:
        return self.bench.config_path / "services"

    @override
    def write_config(self) -> None:
        admin_units_before = self._admin_unit_text()
        AdminEnvManager(cli_root()).ensure()
        self._ensure_redis_config()
        self._ensure_gunicorn_config()
        GunicornManager(self.bench).generate_admin_config()
        self.systemd_conf_dir.mkdir(parents=True, exist_ok=True)

        # Built before unlinking anything: a broken declaration raises here, and a
        # half-cleared directory would drop services on the next daemon-reload.
        defs = self._prod_process_definitions()

        target_file = self._target_name()
        for path in list(self.systemd_conf_dir.iterdir()):
            if path.is_file() and (path.suffix in (".service", ".socket") or path.name == target_file):
                path.unlink()

        renderer = SystemdRenderer(self.bench.config.name)
        workload_units: list[str] = []
        for pd in defs:
            if pd.name == "admin":
                (self.systemd_conf_dir / self._unit_name("admin")).write_text(self._admin_service_text())
                (self.systemd_conf_dir / self._admin_socket_name()).write_text(
                    renderer.render_admin_socket(self.bench.config.admin.internal_port)
                )
            else:
                (self.systemd_conf_dir / self._unit_name(pd.name)).write_text(renderer.render(pd))
                workload_units.append(self._unit_name(pd.name))
        (self.systemd_conf_dir / self._target_name()).write_text(renderer.render_target(workload_units))
        self.admin_units_changed = self._admin_unit_text() != admin_units_before

    def _admin_unit_text(self) -> list[str]:
        """The admin unit files as they stand on disk."""
        names = (self._unit_name("admin"), self._admin_socket_name())
        return [
            path.read_text() if (path := self.systemd_conf_dir / name).is_file() else "" for name in names
        ]

    @override
    def install_config(self) -> None:

        self.user_unit_dir.mkdir(parents=True, exist_ok=True)
        defs = self._prod_process_definitions()
        units = set(self._unit_name(pd.name) for pd in defs) | {
            self._target_name(),
            self._admin_socket_name(),
        }

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

        self._ensure_linger()

        env = self._systemctl_env()
        run_command(self._systemctl("daemon-reload"), env=env)
        # reset-failed clears rate-limit state so re-deploys can restart the admin socket.
        subprocess.run(self._systemctl("reset-failed", *units), capture_output=True, env=env)
        run_command(self._systemctl("enable", self._target_name()), env=env)
        # Re-activating admin costs a graceful gunicorn stop; a workload-only change
        # must not pay for it.
        if self.admin_units_changed or not self.are_units_running(UnitGroup.ADMIN):
            self._activate_admin_socket(env)

    @staticmethod
    def _ensure_linger() -> None:
        """Units must survive logout. The installer enables this, so only reach
        for sudo - which cannot prompt from a task - when it somehow did not."""
        user = getpass.getuser()
        state = subprocess.run(
            ["loginctl", "show-user", user, "--property=Linger"],
            capture_output=True,
            text=True,
            check=False,
        )
        if state.stdout.strip() == "Linger=yes":
            return
        for command in (
            ["loginctl", "enable-linger", user],
            ["systemctl", "start", f"user@{os.getuid()}.service"],
        ):
            subprocess.run(_privileged(command), capture_output=True, check=False)

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
            result = subprocess.run(
                self._systemctl("is-active", self._target_name()), capture_output=True, env=env
            )
            return result.returncode == 0
        except FileNotFoundError:
            return False

    def start_admin(self) -> None:
        # Base start_admin only daemon-reloads; we need full install for a new bench.
        self.bench.logs_path.mkdir(parents=True, exist_ok=True)
        self.write_config()
        self.install_config()

    def apply_process_action(self, action: str, process_name: str) -> None:
        """start/stop/restart one declared process by its ProcessDefinition name."""
        run_command(self._systemctl(action, self._unit_name(process_name)), env=self._systemctl_env())

    def live_states(self) -> dict[str, dict]:
        """{process_name: {status, pid}} for every non-admin unit this bench owns."""
        env = self._systemctl_env()
        units = sorted(u for u in self._installed_bench_units() if u.endswith(".service"))
        admin_unit = self._unit_name("admin")
        states: dict[str, dict] = {}
        for unit in units:
            if unit == admin_unit:
                continue
            name = unit.removeprefix(f"{self.bench.config.name}-").removesuffix(".service")
            show = subprocess.run(
                self._systemctl("show", unit, "--property=SubState,MainPID"),
                capture_output=True,
                text=True,
                env=env,
            )
            props = dict(line.split("=", 1) for line in show.stdout.splitlines() if "=" in line)
            pid = int(props.get("MainPID", "0") or "0")
            states[name] = {
                "status": _STATE_TO_STATUS.get(props.get("SubState", ""), "unknown"),
                "pid": pid or None,
            }
        return states

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
        return SystemdRenderer(self.bench.config.name).render_admin_service(pd, self._admin_socket_name())

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
