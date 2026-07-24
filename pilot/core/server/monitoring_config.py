from __future__ import annotations

import os
import typing
from pathlib import Path

from pilot.exceptions import BenchError
from pilot.managers.platform import is_linux
from pilot.utils import cli_root, iter_sibling_benches, run_command

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
StandardOutput=append:{cli_root}/logs/bench-monitor.log
StandardError=append:{cli_root}/logs/bench-monitor.error.log

[Install]
WantedBy=default.target
"""


class MonitorConfigurator:
    """Installs monitor units and configures per-bench log files."""

    def __init__(self, bench: "Bench | None" = None):
        self.bench = bench
        self.unit_name = "bench-monitor.service"
        self.timer_unit_name = "bench-monitor.timer"
        monitor_dir = cli_root() / "benches" / ".monitor"
        self.monitor_service_path = monitor_dir / self.unit_name
        self.monitor_timer_path = monitor_dir / self.timer_unit_name
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
        from pilot.config import MonitorConfig

        bench = self._require_bench()
        return bench.config.monitor.log_path or MonitorConfig.default_log_path(bench.config.name)

    @property
    def system_log_path(self) -> Path:
        return self._require_bench().config.monitor.system_log_path

    @property
    def db_log_path(self) -> Path:
        return self._require_bench().config.monitor.db_log_path

    @property
    def slow_query_log_path(self) -> Path:
        return self._require_bench().config.monitor.slow_query_log_path

    def setup(self) -> None:
        if not is_linux():
            raise BenchError("Monitoring is only supported on linux based machines.")

        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def is_system_log_authority(self) -> bool:
        bench = self._require_bench()
        authority_path = bench.config.monitor.authority_file_path
        if not authority_path.exists():
            authority_path.write_text(bench.config.name)
            return True

        authority_bench = authority_path.read_text()
        if bench.config.name == authority_bench:
            return True

        for _, bench_config in iter_sibling_benches(bench.path):
            if bench_config.name == authority_bench and bench_config.production.process_manager in (
                "systemd",
                "supervisor",
            ):
                return False

        authority_path.write_text(bench.config.name)
        return True

    def _systemctl_env(self) -> dict:
        env = dict(os.environ)
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        return env

    def _systemctl(self, *args: str) -> list[str]:
        return ["systemctl", "--user", *args]

    def _render_unit(self) -> str:
        from pilot.managers.environment import AdminEnvManager

        root = cli_root()
        return MONITOR_DAEMON_TEMPLATE.format(
            cli_root=root,
            python=AdminEnvManager(root).python,
        )

    def _write_unit(self) -> None:
        self.monitor_service_path.parent.mkdir(parents=True, exist_ok=True)
        self.monitor_service_path.write_text(self._render_unit())

    def _install_user_unit(self) -> None:
        self._install_user_symlink(self.unit_name, self.monitor_service_path)

    def _write_timer_unit(self) -> None:
        self.monitor_timer_path.parent.mkdir(parents=True, exist_ok=True)
        self.monitor_timer_path.write_text(MONITOR_TIMER_TEMPLATE)

    def _install_user_timer_unit(self) -> None:
        self._install_user_symlink(self.timer_unit_name, self.monitor_timer_path)

    def _install_user_symlink(self, name: str, target: Path) -> None:
        self.user_unit_dir.mkdir(parents=True, exist_ok=True)
        link = self.user_unit_dir / name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target.resolve())

    def _require_bench(self) -> "Bench":
        assert self.bench is not None, "MonitorConfigurator needs a bench for this operation"
        return self.bench
