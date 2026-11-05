from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

from pilot.internal.patch_state import is_applied
from pilot.patches.size_gunicorn_workers_by_cpu import PATCH_NAME, run


def _bench_dir(tmp_path: Path, name: str, workers: int) -> Path:
    bench_dir = tmp_path / name
    (bench_dir / "sites").mkdir(parents=True)
    (bench_dir / "bench.toml").write_text(
        f'[bench]\nname = "{name}"\npython = "3.14"\n\n[gunicorn]\nworkers = {workers}\nthreads = 8\n'
    )
    return bench_dir


def _gunicorn(bench_dir: Path) -> dict:
    return tomllib.loads((bench_dir / "bench.toml").read_text())["gunicorn"]


def test_only_the_old_default_follows_the_cpus(tmp_path: Path) -> None:
    old_default = _bench_dir(tmp_path, "old-default", workers=2)
    hand_set = _bench_dir(tmp_path, "hand-set", workers=6)

    with patch("pilot.managers.platform.available_cpu_count", return_value=4):
        run(tmp_path)
        run(tmp_path)

    assert _gunicorn(old_default) == {"threads": 8}
    assert "workers = 4" in (old_default / "config" / "gunicorn.conf.py").read_text()
    assert _gunicorn(hand_set) == {"workers": 6, "threads": 8}
    assert not (hand_set / "config" / "gunicorn.conf.py").exists()
    assert is_applied(old_default, PATCH_NAME) and is_applied(hand_set, PATCH_NAME)
