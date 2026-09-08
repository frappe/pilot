from __future__ import annotations

import shlex

from pilot.managers.processes.base import ServiceRenderer, override
from pilot.managers.processes.definitions import reject_control_chars
from pilot.managers.processes.local import ProcessDefinition


def _exec(argv: list[str]) -> str:
    # ExecStart must be absolute, so a bare name goes through `env` for the PATH lookup.
    if "/" not in argv[0]:
        argv = ["/usr/bin/env", *argv]
    return shlex.join(argv)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _render_env(env: dict) -> str:
    # Quoted: an unquoted value with a space becomes a second, bogus assignment.
    return "".join(f'Environment="{key}={_escape(value)}"\n' for key, value in env.items())


class SystemdRenderer(ServiceRenderer):
    """Builds systemd --user unit/socket/target text for a bench."""

    @override
    def render(self, pd: ProcessDefinition) -> str:
        reject_control_chars(pd)
        working_dir = f"WorkingDirectory={pd.working_dir}\n" if pd.working_dir else ""
        env = _render_env(pd.env)
        stop = f"TimeoutStopSec={pd.stop_timeout}\n" if pd.stop_timeout is not None else ""
        pre = f"ExecStartPre={_exec(pd.pre_run)}\n" if pd.pre_run else ""
        post = f"ExecStopPost={_exec(pd.post_run)}\n" if pd.post_run else ""
        restart = "on-failure" if pd.restart_on_failure else "no"
        return (
            f"[Unit]\n"
            f"Description={self.bench_name} {pd.name}\n"
            f"PartOf={self.bench_name}.target\n\n"
            f"[Service]\n"
            f"Type=simple\n"
            f"{working_dir}{env}{pre}"
            f"ExecStart={_exec(pd.argv)}\n"
            f"{post}"
            f"Restart={restart}\n"
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
        reject_control_chars(pd)
        env = _render_env(pd.env)
        return (
            f"[Unit]\n"
            f"Description={self.bench_name} admin\n"
            f"Requires={socket_name}\n"
            f"After={socket_name}\n\n"
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
            f"StandardError=append:{pd.log_file}.error.log\n"
        )

    def render_target(self, unit_names: list[str]) -> str:
        return (
            f"[Unit]\n"
            f"Description={self.bench_name} bench\n"
            f"Wants={' '.join(unit_names)}\n\n"
            f"[Install]\n"
            f"WantedBy=default.target\n"
        )
