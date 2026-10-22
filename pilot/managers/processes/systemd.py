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
from pilot.managers.processes.definitions import ProcessState
from pilot.managers.processes.local import ProcessDefinition
from pilot.managers.processes.systemd_render import SystemdRenderer
from pilot.managers.systemd_user import SystemdUserMixin
from pilot.utils import cli_root, run_command

# Seconds of inactivity before the socket-activated admin stops. This outlives the
# window a host image builder keeps a machine running before it captures a memory
# snapshot, so the admin is still resident in the snapshot instead of cold on resume.
_ADMIN_IDLE_TIMEOUT = 600
_SYSTEMCTL_TIMEOUT = 90


class SystemdProcessManager(SystemdUserMixin, ManagedProcessManager):
    """Manages bench processes via systemd --user (no sudo required)."""

    # Until write_config compares them, assume both admin units changed.
    admin_service_changed = True
    admin_socket_changed = True
    # Workload sockets write_config rewrote, which install_config must hand their ports to.
    changed_sockets: tuple[str, ...] = ()

    @property
    def systemd_conf_dir(self) -> Path:
        return self.bench.config_path / "services"

    @override
    def write_config(self) -> None:
        service_before, socket_before = self._admin_unit_text()
        sockets_before = self._workload_socket_text()
        AdminEnvManager(cli_root()).ensure()
        self._ensure_redis_config()
        self._ensure_gunicorn_config()
        GunicornManager(self.bench).generate_admin_config()
        self.systemd_conf_dir.mkdir(parents=True, exist_ok=True)

        # Build first, so a broken app declaration leaves the current units in place.
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
                if socket := renderer.get_socket_name(pd):
                    (self.systemd_conf_dir / socket).write_text(renderer.render_socket(pd))
                    workload_units.append(socket)
        (self.systemd_conf_dir / self._target_name()).write_text(renderer.render_target(workload_units))
        if self.bench.config.central.enabled:
            (self.systemd_conf_dir / self._central_bootstrap_name()).write_text(
                renderer.render_central_bootstrap(
                    str(AdminEnvManager(cli_root()).python),
                    str(cli_root()),
                    str(self.bench.path),
                    str(self.bench.logs_path / "central-bootstrap.log"),
                )
            )
        service_after, socket_after = self._admin_unit_text()
        self.admin_service_changed = service_after != service_before
        self.admin_socket_changed = socket_after != socket_before
        sockets_after = self._workload_socket_text()
        self.changed_sockets = tuple(
            name for name, text in sockets_after.items() if sockets_before.get(name) != text
        )

    def _workload_socket_text(self) -> dict[str, str]:
        """The workload socket units as they stand on disk, by name."""
        sockets = self.systemd_conf_dir.glob("*.socket") if self.systemd_conf_dir.is_dir() else []
        return {path.name: path.read_text() for path in sockets if path.name != self._admin_socket_name()}

    def _admin_unit_text(self) -> list[str]:
        """The admin unit files as they stand on disk."""
        names = (self._unit_name("admin"), self._admin_socket_name())
        return [
            path.read_text() if (path := self.systemd_conf_dir / name).is_file() else "" for name in names
        ]

    @override
    def install_config(self) -> None:
        units = self.link_units()
        self._ensure_linger()

        env = self._systemctl_env()
        run_command(self._systemctl("daemon-reload"), env=env)
        # reset-failed clears rate-limit state so re-deploys can restart the admin socket.
        subprocess.run(self._systemctl("reset-failed", *units), capture_output=True, env=env)
        run_command(self._systemctl("enable", self._target_name()), env=env)
        self.enable_admin_at_boot()
        self._enable_central_bootstrap(env)
        self._activate_admin(env)
        self._activate_sockets(env)

    def link_units(self) -> set[str]:
        """Link the bench's generated units into the user unit dir; stop and unlink dropped ones."""
        self.user_unit_dir.mkdir(parents=True, exist_ok=True)
        defs = self._prod_process_definitions()
        renderer = SystemdRenderer(self.bench.config.name)
        sockets = {socket for pd in defs if (socket := renderer.get_socket_name(pd))}
        units = (
            set(self._unit_name(pd.name) for pd in defs)
            | sockets
            | {self._target_name(), self._admin_socket_name()}
            | self._central_bootstrap_units()
        )

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
        return units

    def reopen_sockets(self) -> None:
        """Give each workload socket the address in its unit file, as after a port change."""
        self.changed_sockets = tuple(self._workload_socket_text())
        self._activate_sockets(self._systemctl_env())

    def _activate_sockets(self, env: dict) -> None:
        """A service that bound its port itself has to let go before its new socket can listen.
        A stopped workload keeps its sockets closed, or a request would start web alone."""
        is_workload_running = self.is_running()
        for socket in self.changed_sockets:
            service = socket.removesuffix(".socket") + ".service"
            was_active = subprocess.run(self._systemctl("is-active", service), capture_output=True, env=env)
            subprocess.run(self._systemctl("stop", service), capture_output=True, env=env)
            if not is_workload_running:
                continue
            run_command(self._systemctl("restart", socket), env=env, timeout=_SYSTEMCTL_TIMEOUT)
            if was_active.returncode == 0:
                run_command(self._systemctl("start", service), env=env, timeout=_SYSTEMCTL_TIMEOUT)
        self.changed_sockets = ()

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
        # Links the units an upgrade added, such as the web socket a web unit requires.
        self.install_config()

    @override
    def restart(self) -> None:
        # A target restart also restarts the sockets, which drops the requests waiting in them.
        if not self.is_running():
            return super().restart()
        units = [self._unit_name(pd.name) for pd in self.process_definitions if pd.name != "admin"]
        run_command(self._systemctl("restart", *units), env=self._systemctl_env())
        return None

    @override
    def apply_unit_action(self, action: str, role: UnitGroup) -> None:
        env = self._systemctl_env()
        if role is UnitGroup.ADMIN:
            self._control_admin(action, env)
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

    def enable_admin_at_boot(self) -> None:
        run_command(self._systemctl("enable", self._unit_name("admin")), env=self._systemctl_env())

    @override
    def wake_admin(self) -> None:
        run_command(
            self._systemctl("start", "--no-block", self._unit_name("admin")), env=self._systemctl_env()
        )

    def start_admin(self) -> None:
        # Base start_admin only daemon-reloads; we need full install for a new bench.
        self.bench.logs_path.mkdir(parents=True, exist_ok=True)
        self.write_config()
        self.install_config()

    @override
    def get_process_states(self, definitions: list[ProcessDefinition]) -> dict[str, ProcessState]:
        units = {self._unit_name(definition.name): definition.name for definition in definitions}
        result = subprocess.run(
            self._systemctl("show", *units, "--property=Id,ActiveState,SubState,MainPID"),
            capture_output=True,
            text=True,
            env=self._systemctl_env(),
        )
        states = {}
        for block in result.stdout.strip().split("\n\n"):
            properties = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            name = units.get(properties.get("Id", ""))
            if name:
                state = f"{properties.get('ActiveState')} ({properties.get('SubState')})"
                states[name] = ProcessState(state, int(properties.get("MainPID") or 0) or None)
        return states

    @override
    def restart_processes(self, definitions: list[ProcessDefinition]) -> None:
        units = [self._unit_name(definition.name) for definition in definitions]
        run_command(self._systemctl("restart", *units), env=self._systemctl_env(), timeout=_SYSTEMCTL_TIMEOUT)

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

    def _central_bootstrap_name(self) -> str:
        return f"{self.bench.config.name}-central-bootstrap.service"

    def _central_bootstrap_units(self) -> set[str]:
        if not self.bench.config.central.enabled:
            return set()
        return {self._central_bootstrap_name()}

    def _enable_central_bootstrap(self, env: dict) -> None:
        for unit in self._central_bootstrap_units():
            run_command(self._systemctl("enable", "--now", unit), env=env)

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
            self._activate_admin(env)
        elif action == "stop":
            for unit in (self._admin_socket_name(), self._unit_name("admin")):
                subprocess.run(self._systemctl("stop", unit), capture_output=True, env=env)
        elif action == "restart":
            service = self._unit_name("admin")
            if (self.user_unit_dir / service).exists():
                subprocess.run(self._systemctl("reset-failed", service), capture_output=True, env=env)
                run_command(self._systemctl("restart", service), env=env, timeout=_SYSTEMCTL_TIMEOUT)

    def _activate_admin(self, env: dict) -> None:
        """The kernel queues requests on a listening socket while the service restarts.
        Restarting the socket drops them, so only a changed or idle socket restarts."""
        socket_state = subprocess.run(
            self._systemctl("is-active", self._admin_socket_name()), capture_output=True, env=env
        )
        if self.admin_socket_changed or socket_state.returncode != 0:
            self._activate_admin_socket(env)
        elif self.admin_service_changed:
            self._control_admin("restart", env)
        self.admin_service_changed = self.admin_socket_changed = False

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
