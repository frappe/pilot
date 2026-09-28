"""Port offsets shared by benches and their worktrees."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilot.config import BenchConfig, WorktreeConfig
from pilot.core.bench.ports import pick_port_offset


def _write_bench(benches_root: Path, name: str, offset: int, worktree_offsets: tuple[int, ...] = ()) -> None:
    config = BenchConfig.from_flat(name, port_offset=offset)
    config.worktrees = [
        WorktreeConfig(f"wt-{worktree_offset}", "gameplan", "gp.localhost", worktree_offset)
        for worktree_offset in worktree_offsets
    ]
    bench_dir = benches_root / name
    bench_dir.mkdir(parents=True)
    (bench_dir / "bench.toml").write_text(config.dumps())


def test_skips_offsets_of_benches_worktrees_and_live_ports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_bench(tmp_path, "one", 0, worktree_offsets=(1,))
    _write_bench(tmp_path, "two", 2, worktree_offsets=(3,))
    # Offset 4 is held by a Vite server, offset 5 by a web server.
    monkeypatch.setattr("pilot.core.bench.ports._port_is_live", lambda port: port in (8084, 8005))

    assert pick_port_offset(tmp_path) == 6


def test_first_offset_on_an_empty_host_is_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pilot.core.bench.ports._port_is_live", lambda port: False)

    assert pick_port_offset(tmp_path) == 0
