import pytest

from pilot.core.bench import Bench
from pilot.core.bench.creator import BenchCreator


@pytest.mark.parametrize(
    "config,expected_port",
    [("[bench\nname =", 8000), ("[bench]\nname = 'partial'\nhttp_port = 8000\n", 8002)],
)
def test_bench_creation_handles_malformed_or_partial_siblings(tmp_path, monkeypatch, config, expected_port):
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "bench.toml").write_text(config)
    monkeypatch.setattr(BenchCreator, "_port_is_live", staticmethod(lambda port: False))
    created = Bench.create_at(tmp_path / "fresh", "fresh")
    assert created.path == tmp_path / "fresh"
    assert created.config.http_port == expected_port
