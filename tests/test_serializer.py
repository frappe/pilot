"""Serializer: to_toml output, and FLAT_KEYS / PORT_FIELDS consistency with the
actual BenchConfig attributes."""

from __future__ import annotations

import tomllib

from pilot.config.serializer import FLAT_KEYS, PORT_FIELDS, default_config, to_toml


def _navigable(config, dotted: str) -> bool:
    obj = config
    for part in dotted.split("."):
        if not hasattr(obj, part):
            return False
        obj = getattr(obj, part)
    return True


def test_flat_keys_resolve_to_real_attributes() -> None:
    config = default_config("x")
    for flat, path in FLAT_KEYS.items():
        assert _navigable(config, path), f"FLAT_KEYS['{flat}'] -> '{path}' is not a real attribute"


def test_port_fields_resolve_to_real_attributes() -> None:
    config = default_config("x")
    for path in PORT_FIELDS:
        assert _navigable(config, path), f"PORT_FIELDS '{path}' is not a real attribute"


def test_to_toml_is_parseable_and_round_trips_core_fields() -> None:
    config = default_config("mybench")
    config.mariadb.root_password = "secret"
    config.http_port = 8005
    data = tomllib.loads(to_toml(config))
    assert data["bench"]["name"] == "mybench"
    assert data["bench"]["http_port"] == 8005
    assert data["bench"]["python"] == config.python_version  # python_version → "python"
    assert data["mariadb"]["root_password"] == "secret"


def test_omit_empty_metadata_drops_falsy_fields() -> None:
    config = default_config("x")
    config.mariadb.instance = ""
    config.default_branch = ""
    toml = to_toml(config)
    assert "instance =" not in toml.split("[postgres]")[0]
    assert "default_branch =" not in toml


def test_flat_keys_map_to_expected_paths() -> None:
    assert FLAT_KEYS["mariadb_password"] == "mariadb.root_password"
    assert "admin_tls" in FLAT_KEYS
