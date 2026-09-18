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
from pilot.managers.systemd_user import SystemdUserMixin, install_user_timer, user_timer_installed
from pilot.utils import cli_root

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

MONITOR_TIMER_TEMPLATE = """\
[Unit]
Description=bench monitor timer

[Timer]
OnBootSec=10s
OnUnitInactiveSec=10s
AccuracySec=1s

[Install]
WantedBy=timers.target
"""

MONITOR_DAEMON_TEMPLATE = """\
[Unit]
Description=bench monitor

[Service]
Type=oneshot
WorkingDirectory={cli_root}
Environment=PYTHONPATH={cli_root}
ExecStart={python} -m pilot.core.server.monitoring
StandardOutput=append:{cli_root}/system/logs/monitor.log
StandardError=append:{cli_root}/system/logs/monitor.error.log

[Install]
WantedBy=default.target
"""


class MonitorConfigurator(SystemdUserMixin):
    """Installs the shared host-wide monitor timer and configures per-bench
    log files. One timer for the whole host, regardless of how many benches
    exist - install() is a no-op once it's already set up."""

    def __init__(self, bench: "Bench | None" = None):
        self.bench = bench
        self.unit_name = "pilot-monitor.service"
        self.timer_unit_name = "pilot-monitor.timer"
        self.monitor_dir = cli_root() / "system" / "monitor"

    def install(self) -> None:
        if user_timer_installed(self.timer_unit_name):
            return
        # systemd cannot open the unit's append: targets if this is missing.
        monitor_log_dir().mkdir(parents=True, exist_ok=True)
        install_user_timer(
            unit_dir=self.monitor_dir,
            unit_name=self.unit_name,
            unit_text=self._render_unit(),
            timer_unit_name=self.timer_unit_name,
            timer_text=MONITOR_TIMER_TEMPLATE,
        )

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
