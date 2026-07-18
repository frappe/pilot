from __future__ import annotations

import shlex

from pilot.managers.processes.base import ServiceRenderer, override
from pilot.managers.processes.local import ProcessDefinition


class SystemdRenderer(ServiceRenderer):
    """Builds systemd --user unit/socket/target text for a bench."""

    @override
    def render(self, pd: ProcessDefinition) -> str:
        working_dir = f"WorkingDirectory={pd.working_dir}\n" if pd.working_dir else ""
        env = "".join(f"Environment={k}={v}\n" for k, v in pd.env.items())
        stop = f"TimeoutStopSec={pd.stop_timeout}\n" if pd.stop_timeout is not None else ""
        return (
            f"[Unit]\n"
            f"Description={self.bench_name} {pd.name}\n"
            f"PartOf={self.bench_name}.target\n\n"
            f"[Service]\n"
            f"Type=simple\n"
            f"{working_dir}{env}"
            f"ExecStart={shlex.join(pd.argv)}\n"
            f"Restart=on-failure\n"
            f"{stop}"
            f"StandardOutput=append:{pd.log_file}\n"
            f"StandardError=append:{pd.log_file}.error.log\n"
        )

    def admin_socket(self, port: int) -> str:
        # No PartOf: admin stays reachable while the workload is stopped.
        return (
            f"[Unit]\n"
            f"Description={self.bench_name} admin (socket)\n\n"
            f"[Socket]\n"
            f"ListenStream=127.0.0.1:{port}\n\n"
            f"[Install]\n"
            f"WantedBy=default.target\n"
        )

    def admin_service(self, pd: ProcessDefinition, socket_name: str) -> str:
        env = "".join(f"Environment={k}={v}\n" for k, v in pd.env.items())
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

    def target(self, unit_names: list[str]) -> str:
        return (
            f"[Unit]\n"
            f"Description={self.bench_name} bench\n"
            f"Wants={' '.join(unit_names)}\n\n"
            f"[Install]\n"
            f"WantedBy=default.target\n"
        )
