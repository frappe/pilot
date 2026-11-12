from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from pathlib import Path

from pilot.managers.processes.base import ServiceRenderer, override
from pilot.managers.processes.local import ProcessDefinition
from pilot.managers.processes.sandbox import CODE_DIRECTORIES, CONTROL_ENTRIES, WORKLOAD_DIRECTORIES

_SAFE_ARGUMENT = re.compile(r"[\w@%+=:,./-]+", re.ASCII)

# NoNewPrivileges keeps sudo out; the mount directives need PrivateUsers in a --user unit.
_SANDBOX_DIRECTIVES = [
    "NoNewPrivileges=yes",
    "PrivateUsers=yes",
    "SystemCallFilter=@system-service",
    "SystemCallArchitectures=native",
    "SystemCallErrorNumber=EPERM",
    "ProtectKernelTunables=yes",
    "ProtectKernelModules=yes",
    "ProtectKernelLogs=yes",
    "ProtectControlGroups=yes",
    "ProtectClock=yes",
    "ProtectHostname=yes",
    "PrivateDevices=yes",
    "RestrictNamespaces=yes",
    "RestrictRealtime=yes",
    "RestrictSUIDSGID=yes",
    "LockPersonality=yes",
    "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK",
    "ProtectSystem=strict",
    "ProtectHome=tmpfs",
    "PrivateTmp=yes",
    # Host sockets such as docker.sock and libvirt-sock open to the user's groups.
    "TemporaryFileSystem=/run:ro",
    "TemporaryFileSystem=/dev/shm",
    "InaccessiblePaths=-/var/log/journal",
]


def _exec(argv: list[str]) -> str:
    # Exec lines need an absolute path, so `env` does the PATH lookup for a bare name.
    if "/" not in argv[0]:
        argv = ["/usr/bin/env", *argv]
    return " ".join(_quote(argument) for argument in argv)


def _quote(argument: str) -> str:
    """systemd reads C escapes inside quotes and expands % specifiers and $ variables everywhere."""
    if not _SAFE_ARGUMENT.fullmatch(argument):
        argument = '"' + argument.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return argument.replace("%", "%%").replace("$", "$$")


def _render_env(env: dict) -> str:
    lines = []
    for key, value in env.items():
        value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
        lines.append(f'Environment="{key}={value}"\n')
    return "".join(lines)


class SystemdRenderer(ServiceRenderer):
    """Builds systemd --user unit/socket/target text for a bench."""

    def __init__(
        self, bench_name: str, database_service: str = "", sandbox_properties: list[str] | None = None
    ) -> None:
        super().__init__(bench_name)
        self.database_service = database_service
        self.sandbox_properties = sandbox_properties or []

    @staticmethod
    def get_sandbox_properties(
        bench_path: Path, benches_dir: Path, read_paths: tuple[str, ...] = (), write_paths: tuple[str, ...] = ()
    ) -> list[str]:
        """systemd.exec directives that confine a workload unit to its own bench.

        `read_paths` and `write_paths` are what every unit gets back from the masked home."""
        # The code folders and Pilot's control files: the admin runs or trusts them outside the sandbox,
        # so a workload must not change them.
        control = [str(bench_path / name) for name in CODE_DIRECTORIES]
        control += [str(bench_path / "config" / name) for name in CONTROL_ENTRIES]
        control.append(str(bench_path / "sites" / "common_site_config.json"))
        folders = " ".join(_quote(str(bench_path / name)) for name in WORKLOAD_DIRECTORIES)
        return [
            *_SANDBOX_DIRECTIVES,
            # ProtectHome only masks /home and /root; this masks a home elsewhere, such as /srv/frappe.
            f"TemporaryFileSystem={_quote(f'{Path.home()}:ro')}",
            # The tmpfs hides the neighbouring benches and this bench's admin files; the binds bring back
            # the folders a workload uses.
            f"TemporaryFileSystem={_quote(f'{benches_dir}:ro')}",
            f"BindPaths={folders}",
            f"ReadWritePaths={folders}",
            # DNS on hosts that run systemd-resolved.
            *SystemdRenderer.get_path_properties([*control, "/run/systemd/resolve", *read_paths], write_paths),
        ]

    @staticmethod
    def get_path_properties(read_paths: Iterable[str], write_paths: Iterable[str]) -> list[str]:
        """Directives that bring paths back into the masked home. "-" skips a path the host lacks."""
        properties = []
        read_paths, write_paths = list(read_paths), list(write_paths)
        if read_paths:
            properties.append("BindReadOnlyPaths=" + " ".join(_quote(f"-{path}") for path in read_paths))
        if write_paths:
            writable = " ".join(_quote(f"-{path}") for path in write_paths)
            properties += [f"BindPaths={writable}", f"ReadWritePaths={writable}"]
        return properties

    @override
    def render(self, pd: ProcessDefinition) -> str:
        working_dir = f"WorkingDirectory={pd.working_dir}\n" if pd.working_dir else ""
        env = _render_env(pd.env)
        stop = f"TimeoutStopSec={pd.stop_timeout}\n" if pd.stop_timeout is not None else ""
        kill_mode = "KillMode=mixed\n" if pd.stop_main_process_only else ""
        pre = f"ExecStartPre={_exec(pd.pre_run)}\n" if pd.pre_run else ""
        post = f"ExecStopPost={_exec(pd.post_run)}\n" if pd.post_run else ""
        restart = "on-failure" if pd.restart_on_failure else "no"
        remove_socket = f"ExecStartPre=/bin/rm -f {pd.socket_file}\n" if pd.socket_file else ""
        sandbox = "".join(f"{line}\n" for line in self._sandbox_lines(pd))
        return (
            f"[Unit]\n"
            f"Description={self.bench_name} {pd.name}\n"
            f"PartOf={self.bench_name}.target\n"
            f"{self._dependencies(pd)}\n"
            f"[Service]\n"
            f"Type=simple\n"
            f"{working_dir}{env}"
            f"{remove_socket}{pre}"
            f"ExecStart={_exec(pd.argv)}\n"
            f"{post}"
            f"Restart={restart}\n"
            f"LimitNOFILE=65535\n"
            f"{stop}{kill_mode}{sandbox}"
            f"StandardOutput=append:{pd.log_file}\n"
            f"StandardError=append:{pd.log_file}.error.log\n"
        )

    def _sandbox_lines(self, pd: ProcessDefinition) -> list[str]:
        if not self.sandbox_properties:
            return []
        return [*self.sandbox_properties, *self.get_path_properties(pd.read_paths, pd.write_paths)]

    def _dependencies(self, pd: ProcessDefinition) -> str:
        # systemd stops in reverse start order; redis must outlive a lite runner draining into it.
        lines = ""
        if not pd.name.startswith("redis"):
            lines += f"After={self.bench_name}-redis_cache.service {self.bench_name}-redis_queue.service\n"
        if socket := self.get_socket_name(pd):
            lines += f"Requires={socket}\nAfter={socket}\n"
        if pd.name == "web" and self.database_service:
            lines += f"Requires={self.database_service}\nAfter={self.database_service}\nPartOf={self.database_service}\n"
        return lines

    def render_admin_socket(self, port: int) -> str:
        # No PartOf: admin stays reachable while the workload is stopped.
        return (
            f"[Unit]\n"
            f"Description={self.bench_name} admin (socket)\n\n"
            f"[Socket]\n"
            f"ListenStream=127.0.0.1:{port}\n\n"
            f"[Install]\n"
            f"WantedBy=default.target\n"
        )

    def render_admin_service(self, pd: ProcessDefinition, socket_name: str) -> str:
        env = _render_env(pd.env)
        return (
            f"[Unit]\n"
            f"Description={self.bench_name} admin\n"
            f"Requires={socket_name}\n"
            f"After={socket_name}\n"
            # A crash loop must not trip the start limit: that fails the socket and
            # nginx answers 502 until someone resets it.
            f"StartLimitIntervalSec=0\n\n"
            f"[Service]\n"
            f"Type=simple\n"
            f"WorkingDirectory={pd.working_dir}\n"
            f"{env}"
            f"ExecStart={shlex.join(pd.argv)}\n"
            # Re-activation is via the socket, not a restart loop.
            f"Restart=no\n"
            # Signal gunicorn only; never cgroup-kill - tasks run as its children
            # and must outlive it idle-stopping or restarting its socket.
            f"KillMode=process\n"
            f"StandardOutput=append:{pd.log_file}\n"
            f"StandardError=append:{pd.log_file}.error.log\n\n"
            # Start at boot to run tasks queued before a reboot.
            f"[Install]\n"
            f"WantedBy=default.target\n"
        )

    def render_central_bootstrap(self, python: str, cli_root: str, bench_root: str, log_file: str) -> str:
        """Render the boot-time Central credential service."""
        return (
            f"[Unit]\n"
            f"Description={self.bench_name} central bootstrap\n\n"
            f"[Service]\n"
            f"Type=simple\n"
            f"WorkingDirectory={cli_root}\n"
            f"Environment=PYTHONUNBUFFERED=1\n"
            f"Environment=PYTHONPATH={cli_root}\n"
            f"ExecStart={python} -m admin.backend.central_bootstrap --bench-root {bench_root}\n"
            f"Restart=on-failure\n"
            f"RestartSec=5s\n"
            f"StandardOutput=append:{log_file}\n"
            f"StandardError=append:{log_file}.error.log\n\n"
            f"[Install]\n"
            f"WantedBy=default.target\n"
        )

    def get_socket_name(self, pd: ProcessDefinition) -> str | None:
        return f"{self.bench_name}-{pd.name}.socket" if pd.listen_address else None

    def render_socket(self, pd: ProcessDefinition) -> str:
        # A target stop stops the socket. A target restart would restart it too, so `restart` avoids one.
        return (
            f"[Unit]\n"
            f"Description={self.bench_name} {pd.name} (socket)\n"
            f"StopPropagatedFrom={self.bench_name}.target\n\n"
            f"[Socket]\n"
            f"ListenStream={pd.listen_address}\n"
        )

    def render_target(self, unit_names: list[str]) -> str:
        return (
            f"[Unit]\n"
            f"Description={self.bench_name} bench\n"
            f"Wants={' '.join(unit_names)}\n\n"
            f"[Install]\n"
            f"WantedBy=default.target\n"
        )
