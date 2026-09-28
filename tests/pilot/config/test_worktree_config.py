"""[[worktrees]] records in bench.toml."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilot.config import BenchConfig, WorktreeConfig
from pilot.exceptions import ConfigError


def _write_bench(bench_dir: Path) -> Path:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(BenchConfig.from_flat("main").dumps())
    return bench_dir


def test_worktrees_round_trip_through_open_and_survive_write_flat(tmp_path: Path) -> None:
    bench_root = _write_bench(tmp_path / "main")
    assert "worktrees" not in (bench_root / "bench.toml").read_text()

    with BenchConfig.open(bench_root) as config:
        config.worktrees.append(WorktreeConfig("feature-x", "gameplan", "gp.localhost", 23))
    BenchConfig.write_flat(bench_root, "main", {"watch_apps_js": False})

    assert "[[worktrees]]" in (bench_root / "bench.toml").read_text()
    assert BenchConfig.read(bench_root).worktrees == [
        WorktreeConfig(name="feature-x", app="gameplan", base_site="gp.localhost", port_offset=23)
    ]
    assert BenchConfig.read(bench_root, strict=True).worktrees


@pytest.mark.parametrize(
    ("worktrees", "message"),
    [
        ([WorktreeConfig("Feature_X", "gameplan", "gp.localhost", 1)], "invalid"),
        ([WorktreeConfig("-x", "gameplan", "gp.localhost", 1)], "invalid"),
        ([WorktreeConfig("x" * 41, "gameplan", "gp.localhost", 1)], "invalid"),
        ([WorktreeConfig("x", "", "gp.localhost", 1)], "app and base_site"),
        ([WorktreeConfig("x", "gameplan", "gp.localhost", 60000)], "out of range"),
        (
            [
                WorktreeConfig("x", "gameplan", "gp.localhost", 1),
                WorktreeConfig("x", "gameplan", "gp.localhost", 2),
            ],
            "names must be unique",
        ),
        (
            [
                WorktreeConfig("x", "gameplan", "gp.localhost", 1),
                WorktreeConfig("y", "gameplan", "gp.localhost", 1),
            ],
            "offsets must be unique",
        ),
    ],
)
def test_invalid_worktrees_are_rejected(worktrees: list[WorktreeConfig], message: str) -> None:
    config = BenchConfig.from_flat("main")
    config.worktrees = worktrees

    with pytest.raises(ConfigError, match=message):
        config.validate()
