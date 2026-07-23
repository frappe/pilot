from __future__ import annotations

import os
import typing
from pathlib import Path

from pilot.exceptions import BenchError
from pilot.managers.platform import is_linux
from pilot.utils import cli_root, run_command

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

SITE_UPTIME_TIMER_TEMPLATE = """\
[Unit]
Description=site uptime monitor timer

[Timer]
OnBootSec=5s
OnUnitInactiveSec=5s
AccuracySec=1s

[Install]
WantedBy=timers.target
"""

SITE_UPTIME_DAEMON_TEMPLATE = """\
[Unit]
Description=site uptime monitor

[Service]
Type=oneshot
WorkingDirectory={cli_root}
Environment=PYTHONPATH={cli_root}
ExecStart={python} -m pilot.core.site.uptime_monitoring
StandardOutput=append:{cli_root}/logs/site-uptime.log
StandardError=append:{cli_root}/logs/site-uptime.error.log

[Install]
WantedBy=default.target
"""


class UptimeMonitorConfigurator:
    """Installs the shared systemd timer that wakes every few seconds and
    pings every production site's /api/method/ping endpoint. One timer covers
    every sibling bench's sites, same shape as MonitorConfigurator. The actual
    polling logic lives in pilot.core.site.uptime_monitoring."""

    def __init__(self, bench: "Bench | None" = None):
        self.bench = bench
        self.unit_name = "site-uptime.service"
        self.timer_unit_name = "site-uptime.timer"
        uptime_dir = cli_root() / "benches" / ".site-uptime-monitor"
        self.uptime_service_path = uptime_dir / self.unit_name
        self.uptime_timer_path = uptime_dir / self.timer_unit_name
        self.user_unit_dir = Path.home() / ".config" / "systemd" / "user"

    def install(self) -> None:
        # systemd cannot open the unit's append: targets if this is missing.
        (cli_root() / "logs").mkdir(parents=True, exist_ok=True)
        self._write_unit()
        self._install_user_unit()
        self._write_timer_unit()
        self._install_user_timer_unit()

        env = self._systemctl_env()
        run_command(self._systemctl("daemon-reload"), env=env)
        run_command(self._systemctl("enable", "--now", self.timer_unit_name), env=env)

    @property
    def log_path(self) -> Path:
        return self._require_bench().logs_path / "uptime.json.log"

    def setup(self) -> None:
        if not is_linux():
            raise BenchError("Uptime monitoring is only supported on linux based machines.")

        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _systemctl_env(self) -> dict:
        env = dict(os.environ)
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        return env

    def _systemctl(self, *args: str) -> list[str]:
        return ["systemctl", "--user", *args]

    def _render_unit(self) -> str:
        from pilot.managers.environment import AdminEnvManager

        root = cli_root()
        return SITE_UPTIME_DAEMON_TEMPLATE.format(
            cli_root=root,
            python=AdminEnvManager(root).python,
        )

    def _write_unit(self) -> None:
        self.uptime_service_path.parent.mkdir(parents=True, exist_ok=True)
        self.uptime_service_path.write_text(self._render_unit())

    def _install_user_unit(self) -> None:
        self._install_user_symlink(self.unit_name, self.uptime_service_path)

    def _write_timer_unit(self) -> None:
        self.uptime_timer_path.parent.mkdir(parents=True, exist_ok=True)
        self.uptime_timer_path.write_text(SITE_UPTIME_TIMER_TEMPLATE)

    def _install_user_timer_unit(self) -> None:
        self._install_user_symlink(self.timer_unit_name, self.uptime_timer_path)

    def _install_user_symlink(self, name: str, target: Path) -> None:
        self.user_unit_dir.mkdir(parents=True, exist_ok=True)
        link = self.user_unit_dir / name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target.resolve())

    def _require_bench(self) -> "Bench":
        assert self.bench is not None, "UptimeMonitorConfigurator needs a bench for this operation"
        return self.bench
