from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.commands.setup.telemetry import SetupTelemetryCommand
from pilot.config import (
    AppConfig,
    BenchConfig,
    MariaDBConfig,
    RedisConfig,
    WorkerConfig,
    WorkerGroup,
)
from pilot.config.common import CommonConfig
from pilot.core.bench import Bench


def _bench(root: Path) -> Bench:
    bench_dir = root / "benches" / "b1"
    bench_dir.mkdir(parents=True, exist_ok=True)
    config = BenchConfig(
        name="b1",
        python_version="3.14",
        apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(groups=[WorkerGroup(queues=["default"], count=1)]),
    )
    bench = Bench(config, bench_dir)
    bench.create_directories()
    config.write(bench_dir)
    return bench


def _configurator() -> MagicMock:
    return MagicMock()


def _enable_cloud_integration(bench: Bench) -> None:
    common = CommonConfig.read(bench.path.parent)
    common.central.enabled = True
    common.write(bench.path.parent)
    bench.config = BenchConfig.read(bench.path)


def test_setup_telemetry_fetches_token_from_central_when_not_passed(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _enable_cloud_integration(bench)
    configurator = _configurator()

    with (
        patch("pilot.integrations.central.CentralClient") as client_cls,
        patch("pilot.managers.fluentbit.LogsConfigurator", return_value=configurator),
    ):
        client_cls.return_value.datum_token.return_value = {
            "token": "jwt-abc",
            "expires_in": 604800,
            "resource_id": "vm-1",
        }
        SetupTelemetryCommand(bench, endpoint="https://datum.internal").run()

    # The one Datum credential, which the metrics shipper presents too.
    telemetry = CommonConfig.read(bench.path.parent).telemetry
    assert telemetry.endpoint == "https://datum.internal"
    assert telemetry.token == "jwt-abc"
    configurator.setup.assert_called_once_with()
    installed = configurator.install.call_args.args[0]
    assert installed.endpoint == "https://datum.internal"
    assert installed.token == "jwt-abc"


def test_setup_telemetry_uses_passed_token_without_central(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    configurator = _configurator()

    with (
        patch("pilot.integrations.central.CentralClient") as client_cls,
        patch("pilot.managers.fluentbit.LogsConfigurator", return_value=configurator),
    ):
        SetupTelemetryCommand(bench, endpoint="https://datum.internal", token="jwt-xyz").run()

    client_cls.assert_not_called()
    assert CommonConfig.read(bench.path.parent).telemetry.token == "jwt-xyz"


def test_setup_telemetry_reports_unconfigured_without_an_endpoint(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    configurator = _configurator()

    with (
        patch("pilot.integrations.central.CentralClient") as client_cls,
        patch("pilot.managers.fluentbit.LogsConfigurator", return_value=configurator),
        patch("sys.stdout", new_callable=MagicMock) as stdout,
    ):
        SetupTelemetryCommand(bench).run()

    client_cls.assert_not_called()
    configurator.setup.assert_not_called()
    assert "not configured" in "".join(str(call) for call in stdout.write.call_args_list)


def test_metrics_are_not_promised_without_the_datum_client(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    reports: list[str] = []
    cmd = SetupTelemetryCommand(bench, endpoint="https://datum.example.com", token="jwt")
    cmd.report = reports.append

    with (
        patch("pilot.core.server.monitoring_datum.HAS_DATUM_CLIENT", False),
        patch("pilot.managers.fluentbit.LogsConfigurator", return_value=_configurator()),
    ):
        cmd.run()

    assert any("Metrics stay in local logs" in line for line in reports)
    assert not any("Metrics will ship" in line for line in reports)
