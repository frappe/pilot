from __future__ import annotations

import typing
from pathlib import Path

from pilot.config.monitor import (
    bench_log_path,
    db_log_path,
    monitor_log_dir,
    slow_query_log_path,
    system_log_path,
)
from pilot.exceptions import BenchError
from pilot.managers.platform import is_linux
from pilot.managers.systemd_user import SystemdUserMixin, install_user_service, user_service_installed
from pilot.utils import cli_root, run_command

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

MONITOR_DAEMON_TEMPLATE = """\
[Unit]
Description=bench monitor

[Service]
Type=simple
WorkingDirectory={cli_root}
Environment=PYTHONPATH={cli_root}
Environment=PYTHONUNBUFFERED=1
ExecStart={python} -m pilot.core.server.monitoring
Restart=always
RestartSec=5s
MemoryMax=128M
StandardOutput=append:{cli_root}/system/logs/monitor.log
StandardError=append:{cli_root}/system/logs/monitor.error.log

[Install]
WantedBy=default.target
"""


class MonitorConfigurator(SystemdUserMixin):
    """Installs the shared host-wide monitor service and configures per-bench
    log files. One service runs host metrics and site uptime pings for every bench."""

    def __init__(self, bench: "Bench | None" = None):
        self.bench = bench
        self.unit_name = "pilot-monitor.service"
        self.monitor_dir = cli_root() / "system" / "monitor"

    def install(self) -> None:
        """Write, enable and start the service. A no-op when the installed unit is current. A running
        service is restarted, as `enable --now` would not."""
        unit_text = self._render_unit()
        was_installed = user_service_installed(self.unit_name)
        if was_installed and (self.monitor_dir / self.unit_name).read_text() == unit_text:
            return
        # systemd cannot open the unit's append: targets if this is missing.
        monitor_log_dir().mkdir(parents=True, exist_ok=True)
        install_user_service(unit_dir=self.monitor_dir, unit_name=self.unit_name, unit_text=unit_text)
        if was_installed:
            self.restart()

    def restart(self) -> None:
        """Restart the service, so it runs the Pilot code on disk."""
        if user_service_installed(self.unit_name):
            run_command(self._systemctl("restart", self.unit_name), env=self._systemctl_env())

    @property
    def log_path(self) -> Path:
        return bench_log_path(self._require_bench().config.name)

    @property
    def system_log_path(self) -> Path:
        return system_log_path()

    @property
    def db_log_path(self) -> Path:
        return db_log_path()

    @property
    def slow_query_log_path(self) -> Path:
        return slow_query_log_path()

    def setup(self) -> None:
        if not is_linux():
            raise BenchError("Monitoring is only supported on linux based machines.")

        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _render_unit(self) -> str:
        from pilot.managers.environment import AdminEnvManager

        root = cli_root()
        return MONITOR_DAEMON_TEMPLATE.format(
            cli_root=root,
            python=AdminEnvManager(root).python,
        )

    def _require_bench(self) -> "Bench":
        assert self.bench is not None, "MonitorConfigurator needs a bench for this operation"
        return self.bench
