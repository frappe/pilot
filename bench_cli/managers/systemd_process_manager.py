from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from bench_cli.managers.admin_env_manager import AdminEnvManager
from bench_cli.managers.gunicorn_manager import GunicornManager
from bench_cli.managers.process_manager import ProcessDefinition, ProcessManager, _cli_root
from bench_cli.utils import run_command

# Hardcoded for testing: stop the socket-activated admin after this many seconds
# of inactivity. The next request re-activates it via the systemd socket.
_ADMIN_IDLE_TIMEOUT = 60


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

    def _admin_socket_name(self) -> str:
        return f"{self.bench.config.name}-admin.socket"

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
        AdminEnvManager(_cli_root()).ensure()
        self._ensure_redis_config()
        self._ensure_gunicorn_config()
        GunicornManager(self.bench).generate_admin_config()
        self.systemd_conf_dir.mkdir(parents=True, exist_ok=True)

        # Remove stale unit files (e.g. after switching process managers or enabling
        # companion mode, which drops socketio/worker services).
        target_file = self._target_name()
        for path in list(self.systemd_conf_dir.iterdir()):
            if path.is_file() and (path.suffix in (".service", ".socket") or path.name == target_file):
                path.unlink()

        defs = self._prod_process_definitions()
        for pd in defs:
            if pd.name == "admin":
                # Socket-activated + idle-stopping; not part of the target.
                (self.systemd_conf_dir / self._unit_name("admin")).write_text(self._render_admin_service())
                (self.systemd_conf_dir / self._admin_socket_name()).write_text(self._render_admin_socket())
            else:
                (self.systemd_conf_dir / self._unit_name(pd.name)).write_text(self._render_unit(pd))
        (self.systemd_conf_dir / self._target_name()).write_text(self._render_target(defs))

    def install_config(self) -> None:
        import getpass

        self.user_unit_dir.mkdir(parents=True, exist_ok=True)
        defs = self._prod_process_definitions()
        units = set(self._unit_name(pd.name) for pd in defs) | {self._target_name(), self._admin_socket_name()}

        # Stop dropped units (e.g. socketio after enabling companion mode) so they
        # release their ports; an orphaned process makes the companion crash-loop on bind.
        self._reap_stale_units(units)

        # Remove stale symlinks pointing to this bench's config dir, including broken ones.
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
        # Clear leftover failed/start-limit state so a re-deploy can restart the
        # admin socket, which otherwise hits systemd's rate limit across re-runs.
        subprocess.run(self._systemctl("reset-failed", *units), capture_output=True, env=env)
        run_command(self._systemctl("enable", self._target_name()), env=env)
        self._activate_admin_socket(env)

    def setup_admin(self) -> None:
        """Bring up just the admin control plane (socket-activated), leaving the
        workload down. Used to serve a new bench's setup wizard at its domain
        before it's initialized; install_config activates the admin socket but
        the workload target is only enabled, not started."""
        # The admin service appends to logs/admin.log; the dir won't exist yet on
        # an uninitialized bench, and systemd fails the unit if it's missing.
        self.bench.logs_path.mkdir(parents=True, exist_ok=True)
        self.generate_config()
        self.install_config()

    def _activate_admin_socket(self, env: dict) -> None:
        """(Re)open the admin socket so a changed ListenStream takes effect.

        systemd marks a running .socket "not functional" when its ListenStream
        changes under a daemon-reload (no open fd left) — that surfaces as a 502
        through nginx. A plain restart reopens the listener. Stop any running
        admin service first so it can't keep holding a stale port; the next
        request re-activates it on the new one via the socket."""
        socket = self._admin_socket_name()
        service = self._unit_name("admin")
        subprocess.run(self._systemctl("stop", service), capture_output=True, env=env)
        run_command(self._systemctl("enable", socket), env=env)
        run_command(self._systemctl("restart", socket), env=env)

    def _installed_bench_units(self) -> set[str]:
        """Names of this bench's currently-loaded .service/.socket units."""
        result = subprocess.run(
            self._systemctl(
                "list-units", "--all", "--no-legend", "--plain",
                "--type=service,socket", f"{self.bench.config.name}-*",
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
        """Stop+disable this bench's loaded units not in ``desired`` (best-effort)."""
        env = self._systemctl_env()
        for unit in self._installed_bench_units() - desired:
            subprocess.run(self._systemctl("stop", unit), capture_output=True, env=env)
            subprocess.run(self._systemctl("disable", unit), capture_output=True, env=env)

    def remove_units(self) -> None:
        """Stop, disable and unlink every unit this bench owns (workload + admin).
        Best-effort: missing units are ignored. Conf files under config/systemd
        stay on disk; only the installed symlinks are removed."""
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
        env = self._systemctl_env()
        run_command(self._systemctl("start", self._target_name()), env=env)
        # Bring the admin control plane back up too (it's socket-activated and
        # independent of the target), so `bench start` mirrors `bench stop`.
        self._activate_admin_socket(env)

    def stop(self) -> None:
        run_command(self._systemctl("stop", self._target_name()), env=self._systemctl_env())

    def stop_admin(self) -> None:
        """Stop the admin control plane (socket + service). `bench start`
        re-activates it; this keeps the unit files installed."""
        env = self._systemctl_env()
        for unit in (self._admin_socket_name(), self._unit_name("admin")):
            subprocess.run(self._systemctl("stop", unit), capture_output=True, env=env)

    def restart(self) -> None:
        run_command(self._systemctl("restart", self._target_name()), env=self._systemctl_env())

    def restart_admin(self) -> None:
        """Restart the admin unit if it's installed, so on-disk code/asset
        changes (e.g. a rebuilt UI) take effect without waiting for the next
        socket activation."""
        service = self._unit_name("admin")
        if not (self.user_unit_dir / service).exists():
            return
        run_command(self._systemctl("restart", service), env=self._systemctl_env())

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

    def admin_is_running(self) -> bool:
        """Admin is reachable if its socket is listening or the service is active
        (socket-activated, so a listening socket counts)."""
        env = self._systemctl_env()
        for unit in (self._admin_socket_name(), self._unit_name("admin")):
            try:
                result = subprocess.run(
                    self._systemctl("is-active", unit), capture_output=True, env=env
                )
            except FileNotFoundError:
                return False
            if result.returncode == 0:
                return True
        return False

    def reload_workers(self, web_only: bool = False) -> None:
        cache_port = self.bench.config.redis.cache_port
        subprocess.run(
            ["redis-cli", "-p", str(cache_port), "del", "assets_json"],
            capture_output=True,
        )
        if self.is_running():
            if web_only:
                run_command(
                    self._systemctl("restart", self._unit_name("web")),
                    env=self._systemctl_env(),
                )
            else:
                run_command(
                    self._systemctl("restart", self._target_name()),
                    env=self._systemctl_env(),
                )

    def _render_unit(self, pd: ProcessDefinition) -> str:
        cmd = pd.command

        env_lines: list[str] = []
        while m := re.match(r"^([A-Z_][A-Z0-9_]*)=(\S+)\s+", cmd):
            env_lines.append(f"Environment={m.group(1)}={m.group(2)}")
            cmd = cmd[m.end() :]
        for key, value in pd.env.items():
            env_lines.append(f"Environment={key}={value}")

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

    def _render_admin_socket(self) -> str:
        cfg = self.bench.config.admin
        # The admin is the control plane: it stays reachable while the workload
        # target is stopped, so the socket is independent of the target (no
        # PartOf) and enabled on its own (WantedBy=default.target).
        return (
            "\n".join(
                [
                    "[Unit]",
                    f"Description={self.bench.config.name} admin (socket)",
                    "",
                    "[Socket]",
                    f"ListenStream=127.0.0.1:{cfg.internal_port}",
                    "",
                    "[Install]",
                    "WantedBy=default.target",
                ]
            )
            + "\n"
        )

    def _render_admin_service(self) -> str:
        cli_root = _cli_root()
        gunicorn = AdminEnvManager(cli_root).gunicorn
        admin_conf = GunicornManager(self.bench).admin_config_path
        log_file = self.bench.logs_path / "admin.log"
        return (
            "\n".join(
                [
                    "[Unit]",
                    f"Description={self.bench.config.name} admin",
                    f"Requires={self._admin_socket_name()}",
                    f"After={self._admin_socket_name()}",
                    "",
                    "[Service]",
                    "Type=simple",
                    f"WorkingDirectory={cli_root}",
                    f"Environment=BENCH_ADMIN_ROOT={self.bench.path}",
                    f"Environment=PYTHONPATH={cli_root}",
                    f"Environment=BENCH_ADMIN_IDLE_TIMEOUT={_ADMIN_IDLE_TIMEOUT}",
                    "Environment=MALLOC_ARENA_MAX=2",
                    f"ExecStart={gunicorn} -c {admin_conf} admin.backend.wsgi:application",
                    # Re-activation happens via the socket, not systemd restart.
                    "Restart=no",
                    # Only signal gunicorn itself on stop; never cgroup-kill. Tasks
                    # (init, setup production, update) run as children of the admin
                    # but must outlive it idle-stopping or restarting its own socket.
                    "KillMode=process",
                    f"StandardOutput=append:{log_file}",
                    f"StandardError=append:{log_file}.error.log",
                ]
            )
            + "\n"
        )

    def _render_target(self, defs: list[ProcessDefinition]) -> str:
        # The target groups the workload only. The admin (socket + service) is an
        # independent control-plane unit, so `bench stop` (stop target) never
        # tears it down.
        wants = " ".join(
            self._unit_name(pd.name) for pd in defs if pd.name != "admin"
        )
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
