"""Unknown-field diagnostics for bench.toml decoding."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from pilot.config import BenchConfig
from pilot.exceptions import ConfigError

MINIMAL: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "redis": {"cache_port": 13000, "queue_port": 11000},
    "admin": {"domain": "admin.test.localhost"},
}


def test_unknown_nested_key_reported_with_full_path() -> None:
    data = copy.deepcopy(MINIMAL)
    data["admin"]["unknown_key"] = "x"
    assert "admin.unknown_key" in BenchConfig.unknown_config_paths(data)


def test_mariadb_postgres_letsencrypt_are_now_unknown_tables() -> None:
    """mariadb/postgres/letsencrypt moved to common_config.toml; a bench.toml
    still carrying them (pre-migration) reports them as unrecognized tables
    rather than parsing their contents."""
    data = copy.deepcopy(MINIMAL)
    data["mariadb"] = {"root_password": "root"}
    data["postgres"] = {"root_password": "secret"}
    data["letsencrypt"] = {"email": "ops@example.com"}
    paths = BenchConfig.unknown_config_paths(data)
    assert set(paths) == {"mariadb", "postgres", "letsencrypt"}


def test_nginx_is_now_an_unknown_table() -> None:
    """nginx config is fixed at its compiled-in defaults; a bench.toml carrying
    a [nginx] table reports it as unrecognized rather than parsing it."""
    data = copy.deepcopy(MINIMAL)
    data["nginx"] = {"http_port": 8080}
    assert BenchConfig.unknown_config_paths(data) == ["nginx"]


def test_monitor_is_now_an_unknown_table() -> None:
    """monitor log paths are fixed under system/logs/; a bench.toml carrying
    a [monitor] table reports it as unrecognized rather than parsing it."""
    data = copy.deepcopy(MINIMAL)
    data["monitor"] = {"log_path": "/var/log/custom.log"}
    assert BenchConfig.unknown_config_paths(data) == ["monitor"]


def test_unknown_bench_key_reported() -> None:
    data = copy.deepcopy(MINIMAL)
    data["bench"]["typo"] = 1
    assert "bench.typo" in BenchConfig.unknown_config_paths(data)


def test_unknown_top_level_table_reported() -> None:
    data = copy.deepcopy(MINIMAL)
    data["whatever"] = {"key": 1}
    assert "whatever" in BenchConfig.unknown_config_paths(data)


def test_unknown_array_entry_keys_reported_with_index() -> None:
    data = copy.deepcopy(MINIMAL)
    data["apps"][0]["typo"] = "x"
    data["firewall"] = {"rules": [{"ip": "203.0.113.4", "bogus": 1}]}
    paths = BenchConfig.unknown_config_paths(data)
    assert "apps[0].typo" in paths
    assert "firewall.rules[0].bogus" in paths


def test_known_and_legacy_keys_not_flagged() -> None:
    data = copy.deepcopy(MINIMAL)
    data["production"] = {
        "enabled": True,
        "process_manager": "supervisor",
        "nginx": True,
        "lightweight": False,
        "use_companion_manager": False,
    }
    data["workers"] = [{"queue": "default", "count": 1}]
    assert BenchConfig.unknown_config_paths(data) == []


def test_default_decode_silently_ignores_unknown_and_still_loads(
    capsys: pytest.CaptureFixture,
) -> None:
    data = copy.deepcopy(MINIMAL)
    data["admin"]["unknown_key"] = "x"
    data["bench"]["typo"] = 1
    data["mariadb"] = {"root_password": "root"}

    config = BenchConfig._from_dict(data)

    assert config.name == "test-bench"
    assert capsys.readouterr().err == ""


def test_strict_decode_raises_with_offending_path() -> None:
    data = copy.deepcopy(MINIMAL)
    data["admin"]["unknown_key"] = "x"
    with pytest.raises(ConfigError) as exc_info:
        BenchConfig._from_dict(data, strict=True)
    assert "admin.unknown_key" in str(exc_info.value)


def test_load_config_strict_raises_default_loads(tmp_path: Path) -> None:
    path = tmp_path / "bench.toml"
    path.write_text(
        '[bench]\nname = "b"\npython = "3.14"\n\n'
        '[admin]\ndomain = "admin.test.localhost"\nbogus = 1\n\n'
        "[redis]\ncache_port = 13000\nqueue_port = 11000\n"
    )

    with pytest.raises(ConfigError) as exc_info:
        BenchConfig.read(path, validate=False, strict=True)
    assert "admin.bogus" in str(exc_info.value)

    # Default read path tolerates the stale key and still decodes.
    assert BenchConfig.read(path, validate=False).admin.domain == "admin.test.localhost"
