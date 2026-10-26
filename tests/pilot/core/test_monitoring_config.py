from pathlib import Path
from unittest.mock import patch

from pilot.core.server.monitoring_config import MonitorConfigurator


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
        with patch("pilot.core.server.monitoring_config.install_user_service") as install:
            configurator.install()
        install.assert_not_called()

    unit = (unit_dir / "pilot-monitor.service").read_text()
    assert "Type=simple" in unit
    assert "Restart=always" in unit
    assert not (unit_dir / "pilot-monitor.timer").exists()


def test_monitor_install_rewrites_and_restarts_an_outdated_unit(tmp_path: Path) -> None:
    unit_dir = tmp_path / "user_units"
    with (
        patch("pilot.core.server.monitoring_config.cli_root", return_value=tmp_path),
        patch("pilot.managers.systemd_user.user_unit_dir", return_value=unit_dir),
        patch("pilot.managers.systemd_user.run_command"),
        patch("pilot.core.server.monitoring_config.run_command") as run_command,
        patch("pilot.managers.environment.AdminEnvManager"),
    ):
        configurator = MonitorConfigurator()
        configurator.install()
        run_command.assert_not_called()
        (tmp_path / "system" / "monitor" / "pilot-monitor.service").write_text("[Service]\nType=oneshot\n")
        configurator.install()

    assert "Type=simple" in (unit_dir / "pilot-monitor.service").read_text()
    assert run_command.call_args.args[0][-2:] == ["restart", "pilot-monitor.service"]


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

    assert (unit_dir / "pilot-storage.service").exists()
    timer = (unit_dir / "pilot-storage.timer").read_text()
    assert "OnCalendar=00/6:00:00" in timer
    assert "Persistent=true" in timer
