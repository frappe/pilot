from __future__ import annotations

import subprocess
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

import psutil
from flask import Blueprint, current_app, jsonify

from pilot.config.bench_config import BenchConfig
from ..readers.volume_reader import VolumeReader

stats_bp = Blueprint("stats", __name__)


@lru_cache(maxsize=16)
def _directory_size(path: str) -> int:
    try:
        result = subprocess.run(["du", "-sb", path], capture_output=True, timeout=10)
        return int(result.stdout.split()[0]) if result.returncode == 0 else 0
    except Exception:
        return 0


def _path_sizes(bench_root: Path, config: BenchConfig) -> list[dict]:
    from pilot.managers.mariadb_manager import MariaDBManager

    benches_dir = str(bench_root)
    mariadb = MariaDBManager(config.mariadb)
    mariadb_dir = mariadb.data_dir() if mariadb.is_dedicated else "/var/lib/mysql"
    return [
        {"label": "Benches", "path": benches_dir, "used_bytes": _directory_size(benches_dir)},
        {"label": "MariaDB", "path": mariadb_dir, "used_bytes": _directory_size(mariadb_dir)},
    ]


@stats_bp.route("/stats")
def stats():
    bench_root = current_app.config["BENCH_ROOT"]
    config = BenchConfig.from_file(bench_root / "bench.toml")
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    volume = VolumeReader(bench_root).read()
    paths = _path_sizes(bench_root, config) if not volume.enabled else []
    return jsonify(
        {
            "cpu_percent": psutil.cpu_percent(),
            "memory_percent": mem.percent,
            "memory_used": mem.total - mem.available,
            "memory_total": mem.total,
            "disk_percent": disk.percent,
            "disk_used": disk.used,
            "disk_total": disk.total,
            "volume": asdict(volume),
            "paths": paths,
        }
    )
