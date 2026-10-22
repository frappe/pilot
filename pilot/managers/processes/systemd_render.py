from __future__ import annotations

import re
import shlex

from pilot.managers.processes.base import ServiceRenderer, override
from pilot.managers.processes.local import ProcessDefinition

_SAFE_ARGUMENT = re.compile(r"[\w@%+=:,./-]+", re.ASCII)


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

    @override
    def render(self, pd: ProcessDefinition) -> str:
        working_dir = f"WorkingDirectory={pd.working_dir}\n" if pd.working_dir else ""
        env = _render_env(pd.env)
        stop = f"TimeoutStopSec={pd.stop_timeout}\n" if pd.stop_timeout is not None else ""
        pre = f"ExecStartPre={_exec(pd.pre_run)}\n" if pd.pre_run else ""
        post = f"ExecStopPost={_exec(pd.post_run)}\n" if pd.post_run else ""
        restart = "on-failure" if pd.restart_on_failure else "no"
        remove_socket = f"ExecStartPre=/bin/rm -f {pd.socket_file}\n" if pd.socket_file else ""
        # systemd stops in reverse start order; redis must outlive a lite runner draining into it.
        after_redis = (
            ""
            if pd.name.startswith("redis")
            else f"After={self.bench_name}-redis_cache.service {self.bench_name}-redis_queue.service\n"
        )
        socket = self.get_socket_name(pd)
        requires_socket = f"Requires={socket}\nAfter={socket}\n" if socket else ""
        return (
            f"[Unit]\n"
            f"Description={self.bench_name} {pd.name}\n"
            f"PartOf={self.bench_name}.target\n"
            f"{after_redis}{requires_socket}\n"
            f"[Service]\n"
            f"Type=simple\n"
            f"{working_dir}{env}"
            f"{remove_socket}{pre}"
            f"ExecStart={_exec(pd.argv)}\n"
            f"{post}"
            f"Restart={restart}\n"
            f"LimitNOFILE=65535\n"
            f"{stop}"
            f"StandardOutput=append:{pd.log_file}\n"
            f"StandardError=append:{pd.log_file}.error.log\n"
        )

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

    def render_central_bootstrap(
        self, python: str, cli_root: str, bench_root: str, log_file: str
    ) -> str:
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
