from pathlib import Path

import pytest

from pilot.config import BenchConfig
from pilot.config.common import CommonConfig
from pilot.exceptions import ConfigError


def _populated_common() -> CommonConfig:
    """Every shared section carrying a valid, recognisably non-default value."""
    common = CommonConfig()
    common.mariadb.root_password = "root-secret"
    common.postgres.root_password = "postgres-secret"
    common.letsencrypt.email = "ops@example.com"
    common.central.enabled = True
    common.central.bootstrapped = True
    common.proxy.protocol_v2 = True
    common.telemetry.endpoint = "https://datum.example.com"
    common.telemetry.endpoint = "https://logs.example.com"
    common.resource_limits.cpu_usage_limit = 77
    common.jwks_url = "https://issuer.example.com/jwks.json"
    common.jwks_audience = "bench-fleet"
    return common


def _bench_with_common(tmp_path: Path, common: CommonConfig) -> Path:
    benches = tmp_path / "benches"
    (benches / "b1").mkdir(parents=True)
    common.write(benches)
    bench_root = benches / "b1"
    config = BenchConfig._from_dict(
        {"bench": {"name": "b1", "python": "3.14"}}, common=CommonConfig.read(benches)
    )
    (bench_root / "bench.toml").write_text(config.dumps())
    return bench_root


def _touch_unrelated_setting(bench_root: Path) -> None:
    with BenchConfig.open(bench_root) as config:
        config.admin.timeout = config.admin.timeout + 1


def test_no_shared_section_is_lost_by_an_unrelated_write(tmp_path: Path) -> None:
    """Every shared section survives an unrelated bench write."""
    expected = _populated_common()
    bench_root = _bench_with_common(tmp_path, expected)

    _touch_unrelated_setting(bench_root)

    assert CommonConfig.read(bench_root.parent) == expected


def test_proxy_protocol_survives_an_unrelated_write(tmp_path: Path) -> None:
    common = CommonConfig()
    common.proxy.protocol_v2 = True
    bench_root = _bench_with_common(tmp_path, common)

    _touch_unrelated_setting(bench_root)

    assert CommonConfig.read(bench_root.parent).proxy.protocol_v2 is True


def test_cloudflare_proxy_survives_an_unrelated_write(tmp_path: Path) -> None:
    common = CommonConfig()
    common.proxy.cloudflare = True
    bench_root = _bench_with_common(tmp_path, common)

    _touch_unrelated_setting(bench_root)

    assert CommonConfig.read(bench_root.parent).proxy.cloudflare is True


def test_a_bench_reads_back_the_proxy_setting(tmp_path: Path) -> None:
    common = CommonConfig()
    common.proxy.protocol_v2 = True
    bench_root = _bench_with_common(tmp_path, common)

    assert BenchConfig.read(bench_root).proxy.protocol_v2 is True


def test_a_bench_write_keeps_a_setting_committed_after_it_read(tmp_path: Path) -> None:
    """A bench reads the shared file without holding its lock, so writing the
    whole view back would undo whatever another bench committed since."""
    bench_root = _bench_with_common(tmp_path, CommonConfig())
    benches = bench_root.parent
    stale = BenchConfig.read(bench_root)

    with CommonConfig.open(benches) as concurrent:
        concurrent.telemetry.endpoint = "https://datum.committed-later"

    stale.proxy.protocol_v2 = True
    stale.write(bench_root)

    saved = CommonConfig.read(benches)
    assert saved.proxy.protocol_v2 is True
    assert saved.telemetry.endpoint == "https://datum.committed-later"


def test_a_bench_write_that_changes_nothing_shared_leaves_the_file_alone(tmp_path: Path) -> None:
    bench_root = _bench_with_common(tmp_path, CommonConfig())
    benches = bench_root.parent
    stale = BenchConfig.read(bench_root)

    with CommonConfig.open(benches) as concurrent:
        concurrent.telemetry.endpoint = "https://logs.committed-later"

    stale.admin.timeout = 200  # a bench-local setting
    stale.write(bench_root)

    assert CommonConfig.read(benches).telemetry.endpoint == "https://logs.committed-later"


def test_a_change_in_one_table_keeps_that_tables_other_settings(tmp_path: Path) -> None:
    """Comparing whole tables is enough to spot a change but not to apply one:
    writing the table back would carry this view's stale copy of the rest of it."""
    from pilot.config.central import HostnameAlias

    common = CommonConfig()
    common.central.enabled = True
    bench_root = _bench_with_common(tmp_path, common)
    benches = bench_root.parent
    stale = BenchConfig.read(bench_root)

    with CommonConfig.open(benches) as concurrent:
        concurrent.central.bootstrapped = True
        concurrent.central.hostname_aliases = [
            HostnameAlias(type="site", pattern="site-*.zone.test", target="s.zone.test")
        ]

    stale.central.enabled = False
    stale.write(bench_root)

    saved = CommonConfig.read(benches).central
    assert saved.enabled is False
    assert saved.bootstrapped is True
    assert [alias.target for alias in saved.hostname_aliases] == ["s.zone.test"]


def test_a_change_in_one_table_leaves_other_tables_alone(tmp_path: Path) -> None:
    bench_root = _bench_with_common(tmp_path, CommonConfig())
    benches = bench_root.parent
    stale = BenchConfig.read(bench_root)

    with CommonConfig.open(benches) as concurrent:
        concurrent.mariadb.root_password = "committed-later"

    stale.central.enabled = True
    stale.write(bench_root)

    saved = CommonConfig.read(benches)
    assert saved.central.enabled is True
    assert saved.mariadb.root_password == "committed-later"


def test_nginx_ports_come_from_common_config(tmp_path: Path) -> None:
    common = CommonConfig()
    common.nginx.http_port = 8081
    common.nginx.https_port = 8443
    bench_root = _bench_with_common(tmp_path, common)

    config = BenchConfig.read(bench_root)
    assert (config.nginx.http_port, config.nginx.https_port) == (8081, 8443)
    assert "[nginx]\nhttp_port = 8081\nhttps_port = 8443" in CommonConfig.path(bench_root.parent).read_text()

    _touch_unrelated_setting(bench_root)
    assert CommonConfig.read(bench_root.parent).nginx.ports == {"http_port": 8081, "https_port": 8443}


def test_default_nginx_ports_are_not_written(tmp_path: Path) -> None:
    bench_root = _bench_with_common(tmp_path, CommonConfig())
    assert "[nginx]" not in CommonConfig.path(bench_root.parent).read_text()
    assert BenchConfig.read(bench_root).nginx.ports == {"http_port": 80, "https_port": 443}


@pytest.mark.parametrize(
    "ports",
    [{"http_port": 0}, {"https_port": 70000}, {"http_port": "8080"}, {"http_port": 8443, "https_port": 8443}],
)
def test_invalid_nginx_ports_fail_loudly(ports: dict) -> None:
    with pytest.raises(ConfigError, match="nginx"):
        CommonConfig.from_raw_dict({"nginx": ports})


def test_webhook_endpoints_round_trip_with_their_events(tmp_path: Path) -> None:
    from pilot.config.webhook import WebhookEndpoint

    common = CommonConfig()
    common.webhook_endpoints = [
        WebhookEndpoint("https://one.example.com", "tok", ["site_down"]),
        WebhookEndpoint("https://two.example.com"),
    ]
    common.write(tmp_path)

    assert CommonConfig.read(tmp_path).webhook_endpoints == common.webhook_endpoints
    assert "[[webhook_endpoints]]" in CommonConfig.path(tmp_path).read_text()


@pytest.mark.parametrize(
    "older",
    [
        '[resource_limits.webhook_endpoints]\n"https://one.example.com" = "tok"\n',
        '[[resource_limits.webhook_endpoints]]\nurl = "https://one.example.com"\ntoken = "tok"\n',
    ],
)
def test_webhooks_under_resource_limits_still_read_and_move_to_the_top_level(tmp_path: Path, older: str) -> None:
    from pilot.config.webhook import WebhookEndpoint

    path = CommonConfig.path(tmp_path)
    path.write_text(older)

    with CommonConfig.open(tmp_path) as common:
        assert common.webhook_endpoints == [WebhookEndpoint("https://one.example.com", "tok")]
        common.resource_limits.cpu_usage_limit = 80

    written = path.read_text()
    assert "[[webhook_endpoints]]" in written
    assert "resource_limits.webhook_endpoints" not in written
    assert CommonConfig.read(tmp_path).webhook_endpoints == [WebhookEndpoint("https://one.example.com", "tok")]
