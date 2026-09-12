from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.config.logs import LogsConfig
from pilot.exceptions import BenchError
from pilot.managers.fluentbit import LogsConfigurator


def _configurator(tmp_path: Path) -> LogsConfigurator:
    configurator = LogsConfigurator()
    configurator.conf_dir = tmp_path / "system" / "fluent-bit"
    configurator.state_dir = tmp_path / "system" / "fluent-bit" / "state"
    return configurator


def test_logs_install_writes_config_parsers_lua_and_unit(tmp_path: Path) -> None:
    configurator = _configurator(tmp_path)
    config = LogsConfig(endpoint="https://datum.internal/v1/logs/ingest", token="secret")

    with (
        patch("pilot.managers.fluentbit.cli_root", return_value=tmp_path),
        patch("pilot.managers.fluentbit.shutil.which", return_value="/usr/bin/fluent-bit"),
        patch("pilot.managers.fluentbit.user_service_installed", return_value=False),
        patch(
            "pilot.managers.fluentbit.install_user_service",
            side_effect=lambda **kw: None,
        ) as install,
        patch("pilot.managers.fluentbit.run_command"),
    ):
        configurator.install(config)

    conf = (tmp_path / "system" / "fluent-bit" / "fluent-bit.conf").read_text()
    assert "[SERVICE]" in conf
    assert "[INPUT]" in conf
    assert "[OUTPUT]" in conf
    assert "Bearer ${DATUM_LOG_TOKEN}" in conf
    assert "datum.internal" in conf
    assert "/v1/logs/ingest" in conf
    assert "tls           On" in conf
    assert str(tmp_path / "system" / "fluent-bit" / "parsers.conf") in conf
    assert str(tmp_path / "system" / "fluent-bit" / "state") in conf

    parsers = (tmp_path / "system" / "fluent-bit" / "parsers.conf").read_text()
    assert "pilot_python" in parsers
    assert "pilot_nginx_access" in parsers

    lua = (tmp_path / "system" / "fluent-bit" / "pilot.lua").read_text()
    assert "function normalize" in lua
    assert "\\\"product\\\":\\\"pilot\\\"" in lua

    token_file = tmp_path / "system" / "fluent-bit" / "token.env"
    assert "DATUM_LOG_TOKEN=secret" in token_file.read_text()

    install.assert_called_once()
    unit_text = install.call_args.kwargs["unit_text"]
    assert "fluent-bit" in unit_text
    assert str(tmp_path / "system" / "fluent-bit" / "fluent-bit.conf") in unit_text


def test_logs_install_disables_tls_for_http_endpoint(tmp_path: Path) -> None:
    configurator = _configurator(tmp_path)
    config = LogsConfig(endpoint="http://datum.internal", token="secret")

    with (
        patch("pilot.managers.fluentbit.cli_root", return_value=tmp_path),
        patch("pilot.managers.fluentbit.shutil.which", return_value="/usr/bin/fluent-bit"),
        patch("pilot.managers.fluentbit.user_service_installed", return_value=False),
        patch("pilot.managers.fluentbit.install_user_service"),
        patch("pilot.managers.fluentbit.run_command"),
    ):
        configurator.install(config)

    conf = (tmp_path / "system" / "fluent-bit" / "fluent-bit.conf").read_text()
    assert "tls           On" not in conf


def test_logs_install_restarts_when_already_installed(tmp_path: Path) -> None:
    configurator = _configurator(tmp_path)
    config = LogsConfig(endpoint="https://datum.internal", token="test-token")

    with (
        patch("pilot.managers.fluentbit.cli_root", return_value=tmp_path),
        patch("pilot.managers.fluentbit.shutil.which", return_value="/usr/bin/fluent-bit"),
        patch("pilot.managers.fluentbit.user_service_installed", return_value=True),
        patch("pilot.managers.fluentbit.install_user_service") as install,
        patch("pilot.managers.fluentbit.run_command") as run,
    ):
        configurator.install(config)

    install.assert_not_called()
    restart_calls = [call for call in run.call_args_list if "restart" in call.args[0]]
    assert len(restart_calls) == 1

    token_file = tmp_path / "system" / "fluent-bit" / "token.env"
    assert "DATUM_LOG_TOKEN=test-token" in token_file.read_text()


def test_logs_install_falls_back_to_default_binary_path(tmp_path: Path) -> None:
    configurator = _configurator(tmp_path)
    config = LogsConfig(endpoint="https://datum.internal", token="secret")

    with (
        patch("pilot.managers.fluentbit.cli_root", return_value=tmp_path),
        patch("pilot.managers.fluentbit.shutil.which", return_value=None),
        patch("pilot.managers.fluentbit.Path.exists", return_value=True),
        patch("pilot.managers.fluentbit.user_service_installed", return_value=False),
        patch("pilot.managers.fluentbit.install_user_service") as install,
        patch("pilot.managers.fluentbit.run_command"),
    ):
        configurator.install(config)

    unit_text = install.call_args.kwargs["unit_text"]
    assert "/opt/fluent-bit/bin/fluent-bit" in unit_text


def test_logs_install_fails_loudly_when_binary_not_found(tmp_path: Path) -> None:
    configurator = _configurator(tmp_path)
    config = LogsConfig(endpoint="https://datum.internal", token="secret")

    with (
        patch("pilot.managers.fluentbit.cli_root", return_value=tmp_path),
        patch("pilot.managers.fluentbit.shutil.which", return_value=None),
        patch("pilot.managers.fluentbit.Path.exists", return_value=False),
        patch("pilot.managers.fluentbit.user_service_installed", return_value=False),
        pytest.raises(BenchError, match="fluent-bit binary not found"),
    ):
        configurator.install(config)


def test_logs_setup_installs_fluent_bit_when_missing(tmp_path: Path) -> None:
    configurator = _configurator(tmp_path)
    manager = MagicMock()

    with (
        patch("pilot.managers.fluentbit.is_linux", return_value=True),
        patch("pilot.managers.fluentbit.shutil.which", return_value=None),
        patch("pilot.managers.fluentbit.get_package_manager", return_value=manager),
        patch("pilot.managers.fluentbit.run_command"),
    ):
        configurator.setup()

    manager.install.assert_called_once_with("fluent-bit")
    assert configurator.state_dir.is_dir()


def test_logs_setup_skips_install_when_fluent_bit_present(tmp_path: Path) -> None:
    configurator = _configurator(tmp_path)
    manager = MagicMock()

    with (
        patch("pilot.managers.fluentbit.is_linux", return_value=True),
        patch("pilot.managers.fluentbit.shutil.which", return_value="/usr/bin/fluent-bit"),
        patch("pilot.managers.fluentbit.get_package_manager", return_value=manager),
        patch("pilot.managers.fluentbit.run_command"),
    ):
        configurator.setup()

    manager.install.assert_not_called()


def test_logs_setup_fails_off_linux(tmp_path: Path) -> None:
    configurator = _configurator(tmp_path)

    with (
        patch("pilot.managers.fluentbit.is_linux", return_value=False),
        pytest.raises(Exception, match="linux"),
    ):
        configurator.setup()


def test_logs_remove_stops_disables_and_cleans(tmp_path: Path) -> None:
    configurator = _configurator(tmp_path)
    configurator.conf_dir.mkdir(parents=True, exist_ok=True)
    (configurator.conf_dir / "fluent-bit.conf").write_text("x")
    (configurator.conf_dir / "parsers.conf").write_text("x")
    (configurator.conf_dir / "token.env").write_text("x")
    (configurator.conf_dir / configurator.unit_name).write_text("x")

    with patch("pilot.managers.fluentbit.run_command") as run:
        configurator.remove()

    commands = [call.args[0] for call in run.call_args_list]
    assert ["systemctl", "--user", "stop", configurator.unit_name] in commands
    assert ["systemctl", "--user", "disable", configurator.unit_name] in commands
    assert not (configurator.conf_dir / "fluent-bit.conf").exists()
    assert not (configurator.conf_dir / "token.env").exists()


def test_log_config_roundtrips() -> None:
    from pilot.config.common import CommonConfig

    common = CommonConfig(logs=LogsConfig(endpoint="https://datum.internal", token="abc", enabled=True))
    data = common._to_toml_dict()
    assert "logs" in data
    assert data["logs"]["endpoint"] == "https://datum.internal"
    assert data["logs"]["token"] == "abc"
    assert data["logs"]["enabled"] is True

    roundtripped = CommonConfig.from_raw_dict(data)
    assert roundtripped.logs.endpoint == "https://datum.internal"
    assert roundtripped.logs.token == "abc"
    assert roundtripped.logs.enabled is True
