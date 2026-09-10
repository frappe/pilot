from pathlib import Path
from unittest.mock import patch

from pilot.core.server.monitoring_config import MonitorConfigurator
from pilot.core.site.uptime_monitoring_config import UptimeMonitorConfigurator


def test_monitor_install_is_idempotent(tmp_path: Path) -> None:
    unit_dir = tmp_path / "user_units"
    with (
        patch("pilot.core.server.monitoring_config.cli_root", return_value=tmp_path),
        patch("pilot.managers.systemd_user.user_unit_dir", return_value=unit_dir),
        patch("pilot.managers.systemd_user.run_command"),
        patch("pilot.managers.environment.AdminEnvManager"),
    ):
        configurator = MonitorConfigurator()
        configurator.install()
        with patch("pilot.managers.systemd_user.install_user_timer") as install:
            configurator.install()
        install.assert_not_called()

    assert (unit_dir / "bench-monitor.service").exists()
    assert (unit_dir / "bench-monitor.timer").exists()


def test_uptime_install_is_idempotent(tmp_path: Path) -> None:
    unit_dir = tmp_path / "user_units"
    with (
        patch("pilot.core.site.uptime_monitoring_config.cli_root", return_value=tmp_path),
        patch("pilot.managers.systemd_user.user_unit_dir", return_value=unit_dir),
        patch("pilot.managers.systemd_user.run_command"),
        patch("pilot.managers.environment.AdminEnvManager"),
    ):
        configurator = UptimeMonitorConfigurator()
        configurator.install()
        with patch("pilot.managers.systemd_user.install_user_timer") as install:
            configurator.install()
        install.assert_not_called()

    assert (unit_dir / "site-uptime.service").exists()
    assert (unit_dir / "site-uptime.timer").exists()


def test_site_storage_install_is_idempotent(tmp_path: Path) -> None:
    from pilot.core.site.storage.systemd import SiteStorageConfigurator

    unit_dir = tmp_path / "user_units"
    with (
        patch("pilot.core.site.storage.systemd.cli_root", return_value=tmp_path),
        patch("pilot.managers.systemd_user.user_unit_dir", return_value=unit_dir),
        patch("pilot.managers.systemd_user.run_command"),
        patch("pilot.managers.environment.AdminEnvManager"),
    ):
        configurator = SiteStorageConfigurator()
        configurator.install()
        with patch("pilot.managers.systemd_user.install_user_timer") as install:
            configurator.install()
        install.assert_not_called()

    assert (unit_dir / "site-storage.service").exists()
    timer = (unit_dir / "site-storage.timer").read_text()
    assert "OnCalendar=00/6:00:00" in timer
    assert "Persistent=true" in timer
