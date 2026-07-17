"""Unknown-field diagnostics for bench.toml decoding."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from pilot.config.bench import BenchConfig
from pilot.config.bench_toml import load_config
from pilot.config.config_schema import unknown_config_paths
from pilot.exceptions import ConfigError

MINIMAL: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "mariadb": {"root_password": "root"},
    "redis": {"cache_port": 13000, "queue_port": 11000},
    "admin": {"domain": "admin.test.localhost"},
}


def test_unknown_nested_key_reported_with_full_path() -> None:
    data = copy.deepcopy(MINIMAL)
    data["mariadb"]["unknown_key"] = "x"
    assert "mariadb.unknown_key" in unknown_config_paths(data)


def test_unknown_bench_key_reported() -> None:
    data = copy.deepcopy(MINIMAL)
    data["bench"]["typo"] = 1
    assert "bench.typo" in unknown_config_paths(data)


def test_unknown_top_level_table_reported() -> None:
    data = copy.deepcopy(MINIMAL)
    data["whatever"] = {"key": 1}
    assert "whatever" in unknown_config_paths(data)


def test_unknown_array_entry_keys_reported_with_index() -> None:
    data = copy.deepcopy(MINIMAL)
    data["apps"][0]["typo"] = "x"
    data["firewall"] = {"rules": [{"ip": "203.0.113.4", "bogus": 1}]}
    paths = unknown_config_paths(data)
    assert "apps[0].typo" in paths
    assert "firewall.rules[0].bogus" in paths


def test_known_and_legacy_keys_not_flagged() -> None:
    data = copy.deepcopy(MINIMAL)
    data["production"] = {"enabled": True, "process_manager": "supervisor", "nginx": True, "lightweight": False}
    data["workers"] = [{"queue": "default", "count": 1}]
    assert unknown_config_paths(data) == []


def test_default_decode_silently_ignores_unknown_and_still_loads(capsys: pytest.CaptureFixture) -> None:
    data = copy.deepcopy(MINIMAL)
    data["mariadb"]["unknown_key"] = "x"
    data["bench"]["typo"] = 1

    config = BenchConfig._from_dict(data)

    assert config.name == "test-bench"
    assert capsys.readouterr().err == ""


def test_strict_decode_raises_with_offending_path() -> None:
    data = copy.deepcopy(MINIMAL)
    data["mariadb"]["unknown_key"] = "x"
    with pytest.raises(ConfigError) as exc_info:
        BenchConfig._from_dict(data, strict=True)
    assert "mariadb.unknown_key" in str(exc_info.value)


def test_load_config_strict_raises_default_loads(tmp_path: Path) -> None:
    path = tmp_path / "bench.toml"
    path.write_text(
        '[bench]\nname = "b"\npython = "3.14"\n\n'
        '[mariadb]\nroot_password = "r"\nbogus = 1\n\n'
        "[redis]\ncache_port = 13000\nqueue_port = 11000\n"
    )

    with pytest.raises(ConfigError) as exc_info:
        load_config(path, validate=False, strict=True)
    assert "mariadb.bogus" in str(exc_info.value)

    # Default read path tolerates the stale key and still decodes.
    assert load_config(path, validate=False).mariadb.root_password == "r"
