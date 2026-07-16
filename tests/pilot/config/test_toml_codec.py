from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import math
from pathlib import Path

import pytest

from pilot.internal.toml import Toml, TomlDataclassCodec


@dataclass
class ServiceConfig:
    name: str
    port: int
    enabled: bool = True


SERVICE_CODEC = TomlDataclassCodec(
    from_config_dict=lambda data: ServiceConfig(**data["service"]),
    to_config_dict=lambda config: {
        "service": {
            "name": config.name,
            "port": config.port,
            "enabled": config.enabled,
        }
    },
)


def test_toml_dict_round_trip() -> None:
    data = {
        "service": {"name": "worker", "port": 7000, "enabled": True},
        "routes": [{"name": "api", "hosts": ["one.test", "two.test"]}],
    }

    assert Toml.loads(Toml.dumps(data)) == data


def test_simple_loads_and_dumps_api(tmp_path: Path) -> None:
    data = {"service": {"name": "worker", "port": 7000}}
    path = tmp_path / "service.toml"
    path.write_text(Toml.dumps(data), encoding="utf-8")

    assert Toml.loads(path.read_text(encoding="utf-8")) == data


def test_toml_string_round_trip_preserves_escaped_and_unicode_text() -> None:
    data = {
        "quoted": 'the user said "hello"',
        "backslashes": r"C:\Program Files\Pilot\bench.toml",
        "multiline": "first line\nsecond line\r\nthird line",
        "controls": "tab:\t backspace:\b form-feed:\f nul:\x00 unit-separator:\x1f",
        "unicode": "café — 東京 🚀",
    }

    assert Toml.loads(Toml.dumps(data)) == data


def test_toml_key_round_trip_preserves_literal_keys() -> None:
    data = {
        "display name": "Pilot",
        "feature.enabled": True,
        "サイト": "example.test",
        'quoted "key"': "value",
        "nested section": {
            "connection.host": "localhost",
            "認証 方式": "session",
        },
    }

    assert Toml.loads(Toml.dumps(data)) == data


def test_toml_nested_tables_and_arrays_of_tables_round_trip() -> None:
    data = {
        "application": {
            "name": "pilot",
            "empty options": {},
            "database": {
                "settings": {"pool_size": 10, "replicas": []},
            },
        },
        "pipelines": [
            {
                "name": "build",
                "metadata": {},
                "steps": [
                    {"name": "lint", "environment": {"CI": "true"}},
                    {"name": "test", "environment": {}},
                ],
            },
            {"name": "deploy", "metadata": {"region": "eu-west-1"}, "steps": []},
        ],
        "empty root table": {},
    }

    assert Toml.loads(Toml.dumps(data)) == data


def test_toml_native_date_time_and_float_values_round_trip() -> None:
    utc = timezone.utc
    india = timezone(timedelta(hours=5, minutes=30))
    data = {
        "local_date": date(2026, 7, 15),
        "local_time": time(13, 14, 15, 123456),
        "local_datetime": datetime(2026, 7, 15, 13, 14, 15, 123456),
        "utc_datetime": datetime(2026, 7, 15, 13, 14, 15, tzinfo=utc),
        "offset_datetime": datetime(2026, 7, 15, 18, 44, 15, tzinfo=india),
        "finite": 3.141592653589793,
        "positive_infinity": math.inf,
        "negative_infinity": -math.inf,
        "not_a_number": math.nan,
        "negative_zero": -0.0,
    }

    decoded = Toml.loads(Toml.dumps(data))

    for key in (
        "local_date",
        "local_time",
        "local_datetime",
        "utc_datetime",
        "offset_datetime",
        "finite",
    ):
        assert decoded[key] == data[key]
    assert decoded["positive_infinity"] == math.inf
    assert decoded["negative_infinity"] == -math.inf
    assert math.isnan(decoded["not_a_number"])
    assert math.copysign(1.0, decoded["negative_zero"]) == -1.0


@pytest.mark.parametrize(
    "data",
    [
        {"missing": None},
        {"section": {"missing": None}},
        {"values": ["configured", None]},
    ],
    ids=["top-level", "nested-table", "array-item"],
)
def test_toml_dump_rejects_none(data: dict[str, object]) -> None:
    with pytest.raises(TypeError):
        Toml.dumps(data)


def test_dataclass_dict_round_trip() -> None:
    config = ServiceConfig(name="admin", port=7001)

    assert SERVICE_CODEC.from_dict(SERVICE_CODEC.to_dict(config)) == config


def test_dataclass_toml_round_trip() -> None:
    config = ServiceConfig(name="admin", port=7001, enabled=False)

    assert SERVICE_CODEC.loads(SERVICE_CODEC.dumps(config)) == config
