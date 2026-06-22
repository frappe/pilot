from __future__ import annotations

import getpass
import re
import subprocess
from pathlib import Path

from bench_cli.managers.admin_env_manager import AdminEnvManager
from bench_cli.managers.process_manager import ProcessDefinition, ProcessManager, _cli_root
from bench_cli.platform import (
    _privileged,
    service_command,
    service_disable_command,
    service_enable_command,
    service_running,
    which,
)
from bench_cli.utils import run_command


class OpenRCProcessManager(ProcessManager):
    """Manages bench processes via OpenRC (production on Alpine).

    The OpenRC counterpart of SystemdProcessManager: one
    ``supervise-daemon``-backed init script is generated per process under
    ``config/openrc/`` and symlinked into ``/etc/init.d/``. ``supervise-daemon``
    keeps each process alive — the OpenRC equivalent of systemd's
    ``Restart=on-failure`` or supervisor's ``autorestart=true``.

    The workload services and the admin service are separate units so, like the
    systemd/supervisor managers, ``bench stop`` can stop the workload while the
    admin control plane keeps running (and ``setup_admin`` can bring the admin up
    on its own before the bench is initialised). OpenRC has no socket activation,
    so the admin runs as a plain supervised Flask process on ``admin.port`` (the
    same definition the dev/supervisor managers use); nginx proxies straight to
    it.
    """

    @property
    def openrc_conf_dir(self) -> Path:
        return self.bench.config_path / "openrc"

    @property
    def init_d_dir(self) -> Path:
        return Path("/etc/init.d")

    def _service_name(self, process_name: str) -> str:
        return f"{self.bench.config.name}-{process_name}"

    def _all_definitions(self) -> list[ProcessDefinition]:
        return self._prod_process_definitions()

    def _workload_definitions(self) -> list[ProcessDefinition]:
        return [pd for pd in self._all_definitions() if pd.name != "admin"]

    def _admin_definitions(self) -> list[ProcessDefinition]:
        return [pd for pd in self._all_definitions() if pd.name == "admin"]

    def _workload_service_names(self) -> list[str]:
        return [self._service_name(pd.name) for pd in self._workload_definitions()]

    def _admin_service_names(self) -> list[str]:
        return [self._service_name(pd.name) for pd in self._admin_definitions()]

    # ── Config generation ────────────────────────────────────────────────────

    def generate_config(self) -> None:
        AdminEnvManager(_cli_root()).ensure()
        self._ensure_redis_config()
        self._ensure_gunicorn_config()
        self.openrc_conf_dir.mkdir(parents=True, exist_ok=True)
        # Drop stale scripts (e.g. after switching managers or enabling companion
        # mode, which removes the socketio/worker processes).
        wanted = {self._service_name(pd.name) for pd in self._all_definitions()}
        for path in list(self.openrc_conf_dir.iterdir()):
            if path.is_file() and path.name not in wanted:
                path.unlink()
        for pd in self._all_definitions():
            script = self.openrc_conf_dir / self._service_name(pd.name)
            script.write_text(self._render_service(pd))
            script.chmod(0o755)

    def install_config(self) -> None:
        self.openrc_conf_dir.mkdir(parents=True, exist_ok=True)
        for pd in self._all_definitions():
            service = self._service_name(pd.name)
            src = (self.openrc_conf_dir / service).resolve()
            dst = self.init_d_dir / service
            run_command(_privileged(["ln", "-sf", str(src), str(dst)]))
            run_command(service_enable_command(service))

    def is_configured(self) -> bool:
        return (self.init_d_dir / self._service_name("web")).exists()

    def reload(self) -> None:
        # OpenRC has no daemon-reload; symlinked scripts are re-read on next start.
        pass

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self.generate_config()
        self.install_config()
        for service in self._admin_service_names() + self._workload_service_names():
            run_command(service_command("start", service))

    def setup_admin(self) -> None:
        """Bring up just the admin service, leaving the workload down — serves a
        new bench's setup wizard at its domain before it is initialised."""
        self.bench.logs_path.mkdir(parents=True, exist_ok=True)
        self.generate_config()
        self.install_config()
        for service in self._admin_service_names():
            run_command(service_command("start", service))

    def stop(self) -> None:
        """Stop the workload only; the admin keeps running so the control plane
        stays reachable while the workload is down."""
        for service in self._workload_service_names():
            run_command(service_command("stop", service))

    def stop_admin(self) -> None:
        """Stop the admin service; ``bench start`` brings it back."""
        for service in self._admin_service_names():
            run_command(service_command("stop", service))

    def restart(self) -> None:
        for service in self._workload_service_names():
            run_command(service_command("restart", service))

    def remove_services(self) -> None:
        """Stop, disable and unlink every service this bench owns (workload +
        admin). Best-effort; the scripts under config/openrc stay on disk."""
        for service in self._admin_service_names() + self._workload_service_names():
            subprocess.run(service_command("stop", service), capture_output=True)
            subprocess.run(service_disable_command(service), capture_output=True)
            dst = self.init_d_dir / service
            if dst.is_symlink() or dst.exists():
                subprocess.run(_privileged(["rm", "-f", str(dst)]), capture_output=True)

    def is_running(self) -> bool:
        return any(service_running(s) for s in self._workload_service_names())

    def admin_is_running(self) -> bool:
        return any(service_running(s) for s in self._admin_service_names())

    def reload_web(self) -> None:
        cache_port = self.bench.config.redis.cache_port
        subprocess.run(["redis-cli", "-p", str(cache_port), "del", "assets_json"], capture_output=True)
        if self.is_running():
            print("Restarting web worker to pick up new assets...")
            run_command(service_command("restart", self._service_name("web")))

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render_service(self, pd: ProcessDefinition) -> str:
        cmd = pd.command

        env_pairs: list[tuple[str, str]] = []
        while m := re.match(r"^([A-Z_][A-Z0-9_]*)=(\S+)\s+", cmd):
            env_pairs.append((m.group(1), m.group(2)))
            cmd = cmd[m.end():]
        for key, value in pd.env.items():
            env_pairs.append((key, str(value)))

        working_dir = ""
        if m := re.match(r"^cd\s+(\S+)\s*&&\s*", cmd):
            working_dir = m.group(1)
            cmd = cmd[m.end():]

        command, _, command_args = cmd.partition(" ")
        # supervise-daemon needs an absolute command path; bench commands already
        # use absolute python/gunicorn paths, but bare ones (redis-server, node)
        # must be resolved.
        if not command.startswith("/"):
            command = which(command) or command

        service = self._service_name(pd.name)
        # supervise-daemon runs as root (the init script is started by OpenRC),
        # so drop privileges to the bench user for the supervised process — the
        # systemd (--user) and supervisor backends both run the workload/admin
        # unprivileged, and root-owned files under the bench dir/logs are a footgun.
        lines = [
            "#!/sbin/openrc-run",
            f"# {self.bench.config.name} {pd.name} — generated by bench, do not edit",
            f'description="{self.bench.config.name} {pd.name}"',
            "supervisor=supervise-daemon",
            f'command_user="{getpass.getuser()}"',
        ]
        lines += [f'export {key}="{value}"' for key, value in env_pairs]
        if working_dir:
            lines.append(f'directory="{working_dir}"')
        lines.append(f'command="{command}"')
        if command_args:
            lines.append(f'command_args="{command_args}"')
        lines += [
            f'pidfile="/run/{service}.pid"',
            f'output_log="{pd.log_file}"',
            f'error_log="{pd.log_file}.error.log"',
            "respawn_delay=5",
            "",
            "depend() {",
            "\tafter net firewall mariadb",
            "}",
        ]
        return "\n".join(lines) + "\n"
