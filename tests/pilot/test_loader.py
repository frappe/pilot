"""Unit tests for bench resolution in pilot.loader."""
from __future__ import annotations

from pathlib import Path

import pytest

from pilot import loader
from pilot.context import CliContext
from pilot.exceptions import BenchError


def _context(root: Path, bench_name: str | None = None) -> CliContext:
    return CliContext(installation_root=root, bench_name=bench_name)


def _make_bench(root: Path, name: str) -> Path:
    bench_dir = root / "benches" / name
    bench_dir.mkdir(parents=True)
    (bench_dir / "bench.toml").write_text(f'[bench]\nname = "{name}"\n')
    return bench_dir


def test_single_bench_auto_picked_without_explicit(tmp_path: Path, monkeypatch) -> None:
    bench_dir = _make_bench(tmp_path, "only")
    monkeypatch.chdir(tmp_path)
    assert loader.find_bench_root(_context(tmp_path)) == bench_dir


def test_require_explicit_rejects_auto_pick(tmp_path: Path, monkeypatch) -> None:
    _make_bench(tmp_path, "only")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(BenchError, match="explicit bench"):
        loader.find_bench_root(_context(tmp_path), require_explicit=True)


def test_require_explicit_accepts_bench_flag(tmp_path: Path, monkeypatch) -> None:
    bench_dir = _make_bench(tmp_path, "only")
    monkeypatch.chdir(tmp_path)
    assert loader.find_bench_root(_context(tmp_path, "only"), require_explicit=True) == bench_dir


def test_require_explicit_accepts_inside_bench_dir(tmp_path: Path, monkeypatch) -> None:
    bench_dir = _make_bench(tmp_path, "only")
    monkeypatch.chdir(bench_dir)
    assert loader.find_bench_root(_context(tmp_path), require_explicit=True) == bench_dir
