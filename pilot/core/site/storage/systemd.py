from __future__ import annotations

from pilot.config.monitor import monitor_log_dir
from pilot.managers.systemd_user import SystemdUserMixin, install_user_timer, user_timer_installed
from pilot.utils import cli_root

SITE_STORAGE_TIMER_TEMPLATE = """\
[Unit]
Description=site storage usage timer

[Timer]
OnBootSec=5min
OnCalendar=00/6:00:00
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
"""

SITE_STORAGE_COLLECTOR_TEMPLATE = """\
[Unit]
Description=site storage usage collector

[Service]
Type=oneshot
WorkingDirectory={cli_root}
Environment=PYTHONPATH={cli_root}
ExecStart={python} -m pilot.core.site.storage
StandardOutput=append:{cli_root}/system/logs/site-storage.log
StandardError=append:{cli_root}/system/logs/site-storage.error.log

[Install]
WantedBy=default.target
"""


class SiteStorageConfigurator(SystemdUserMixin):
    """Installs the shared six-hourly timer that measures every site on the
    host. One timer for every sibling bench - install() is a no-op once it is
    set up. The measuring lives in pilot.core.site.storage."""

    def __init__(self) -> None:
        self.unit_name = "site-storage.service"
        self.timer_unit_name = "site-storage.timer"
        self.storage_dir = cli_root() / "system" / "storage"

    def install(self) -> None:
        if user_timer_installed(self.timer_unit_name):
            return
        # systemd cannot open the unit's append: targets if this is missing.
        monitor_log_dir().mkdir(parents=True, exist_ok=True)
        install_user_timer(
            unit_dir=self.storage_dir,
            unit_name=self.unit_name,
            unit_text=self._render_unit(),
            timer_unit_name=self.timer_unit_name,
            timer_text=SITE_STORAGE_TIMER_TEMPLATE,
        )

    def _render_unit(self) -> str:
        from pilot.managers.environment import AdminEnvManager

        root = cli_root()
        return SITE_STORAGE_COLLECTOR_TEMPLATE.format(
            cli_root=root,
            python=AdminEnvManager(root).python,
        )
