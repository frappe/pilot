"""Unit tests for `pilot set-bench-config`."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from pilot.commands.bench.set_bench_config import SetBenchConfigCommand
from pilot.exceptions import BenchError
from tests.pilot.commands.test_commands import make_bench


def _run(bench, key: str, value: str) -> None:
    bench.config.write(bench.path)
    SetBenchConfigCommand(bench=bench, key=key, value=value).run()


def _read(bench) -> dict:
    return tomllib.loads((bench.path / "bench.toml").read_text())


def test_set_bench_config_writes_a_bool_as_a_bool_not_a_string(tmp_path: Path) -> None:
    # bool("false") is True, so the string must be parsed before it is stored.
    bench = make_bench(tmp_path)

    _run(bench, "admin_enabled", "true")
    assert _read(bench)["admin"]["enabled"] is True

    _run(bench, "admin_enabled", "1")
    assert _read(bench)["admin"]["enabled"] is True

    _run(bench, "admin_enabled", "false")
    assert _read(bench)["admin"]["enabled"] is False

    _run(bench, "admin_enabled", "0")
    assert _read(bench)["admin"]["enabled"] is False


def test_set_bench_config_rejects_unknown_keys(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.write(bench.path)

    with pytest.raises(BenchError, match="Unknown bench setting 'nope'"):
        SetBenchConfigCommand(bench=bench, key="nope", value="1").run()

    assert "nope" not in (bench.path / "bench.toml").read_text()


def test_set_bench_config_skips_the_runtime_rebuild_before_init(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)

    _run(bench, "allow_developer_mode", "true")

    assert _read(bench)["bench"]["allow_developer_mode"] is True
    assert not (bench.path / "sites").exists()


def test_set_bench_config_rebuilds_runtime_configs_once_initialized(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    (bench.path / "sites").mkdir()
    (bench.path / "config").mkdir()
    (bench.path / "env" / "bin").mkdir(parents=True)
    (bench.path / "env" / "bin" / "python").touch()

    _run(bench, "allow_developer_mode", "true")

    assert _read(bench)["bench"]["allow_developer_mode"] is True
    assert (bench.path / "sites" / "common_site_config.json").exists()
