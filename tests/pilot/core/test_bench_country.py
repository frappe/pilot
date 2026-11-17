from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import BenchConfig
from pilot.config.common import CommonConfig
from pilot.core.bench import Bench
from pilot.exceptions import BenchError, ConfigError


def _new_bench(benches_root: Path, name: str) -> Bench:
    path = benches_root / name
    path.mkdir(parents=True)
    BenchConfig.default(name, benches_root=benches_root).write(path)
    return Bench(path)


def test_country_defaults_to_india(tmp_path: Path) -> None:
    assert _new_bench(tmp_path, "one").country == "India"


def test_first_bench_sets_the_server_country(tmp_path: Path) -> None:
    bench = _new_bench(tmp_path, "one")
    bench.choose_country("Germany")
    bench.settle_country()

    assert CommonConfig.read(tmp_path).country == "Germany"
    assert "country" not in (bench.path / "bench.toml").read_text()
    assert Bench(bench.path).country == "Germany"


def test_later_bench_inherits_the_server_country(tmp_path: Path) -> None:
    first = _new_bench(tmp_path, "one")
    first.choose_country("Germany")
    first.settle_country()

    assert _new_bench(tmp_path, "two").country == "Germany"


def test_later_bench_keeps_a_different_country_to_itself(tmp_path: Path) -> None:
    first = _new_bench(tmp_path, "one")
    first.choose_country("Germany")
    first.settle_country()

    second = _new_bench(tmp_path, "two")
    second.choose_country("France")
    second.settle_country()

    assert CommonConfig.read(tmp_path).country == "Germany"
    assert Bench(second.path).country == "France"
    assert Bench(first.path).country == "Germany"


def test_later_bench_drops_a_copy_of_the_server_country(tmp_path: Path) -> None:
    first = _new_bench(tmp_path, "one")
    first.choose_country("Germany")
    first.settle_country()

    second = _new_bench(tmp_path, "two")
    second.choose_country("Germany")
    second.settle_country()

    assert "country" not in (second.path / "bench.toml").read_text()


def test_unknown_country_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Unknown country"):
        _new_bench(tmp_path, "one").choose_country("Atlantis")


def test_country_is_fixed_once_initialised(tmp_path: Path) -> None:
    bench = _new_bench(tmp_path, "one")
    with patch.object(Bench, "is_initialized", True), pytest.raises(BenchError, match="cannot be changed"):
        bench.choose_country("France")
