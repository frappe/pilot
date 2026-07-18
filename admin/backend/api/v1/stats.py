from __future__ import annotations

import os
import subprocess
import time
from datetime import UTC
from functools import lru_cache
from pathlib import Path

import psutil
from flask import Blueprint, current_app, jsonify, request

from admin.backend.api.responses import error_response
from pilot.config import BenchConfig

stats_bp = Blueprint("stats", __name__)

# cpu_times_percent() reports the delta since its last call, so warm it up once
# here rather than showing a meaningless reading on the first live poll.
psutil.cpu_percent()
psutil.cpu_times_percent()

# Network/disk counters are cumulative, so throughput is the delta between polls
# divided by the elapsed time - this holds the previous reading between requests.
_io_state = {
    "time": time.monotonic(),
    "net": psutil.net_io_counters(),
    "disk": psutil.disk_io_counters(),
}


def _cpu_breakdown() -> dict:
    times = psutil.cpu_times_percent()
    return {
        "user": round(times.user + times.nice, 2),
        "system": round(times.system, 2),
        "iowait": round(getattr(times, "iowait", 0.0), 2),
        "irq": round(getattr(times, "irq", 0.0) + getattr(times, "softirq", 0.0), 2),
        "other": round(getattr(times, "steal", 0.0), 2),
        "idle": round(times.idle, 2),
    }


def _io_rates() -> dict:
    now = time.monotonic()
    net, disk = psutil.net_io_counters(), psutil.disk_io_counters()
    elapsed = max(now - _io_state["time"], 0.001)
    prev_disk = _io_state["disk"]
    rates = {
        "network": {
            "rx_bytes_per_sec": round((net.bytes_recv - _io_state["net"].bytes_recv) / elapsed, 2),
            "tx_bytes_per_sec": round((net.bytes_sent - _io_state["net"].bytes_sent) / elapsed, 2),
        },
        "disk_io": {
            "read_bytes_per_sec": round((disk.read_bytes - prev_disk.read_bytes) / elapsed, 2)
            if disk and prev_disk
            else 0.0,
            "write_bytes_per_sec": round((disk.write_bytes - prev_disk.write_bytes) / elapsed, 2)
            if disk and prev_disk
            else 0.0,
        },
    }
    _io_state.update(time=now, net=net, disk=disk)
    return rates


def _memory_breakdown(mem, swap) -> dict:
    free_mb = mem.free / 1024**2
    cached_mb = (getattr(mem, "cached", 0) + getattr(mem, "buffers", 0)) / 1024**2
    used_mb = max(mem.total / 1024**2 - free_mb - cached_mb, 0)
    return {
        "used_mb": round(used_mb, 2),
        "cached_mb": round(cached_mb, 2),
        "free_mb": round(free_mb, 2),
        "swap_used_mb": round(swap.used / 1024**2, 2),
    }


@lru_cache(maxsize=16)
def _directory_size(path: str) -> int:
    try:
        result = subprocess.run(["du", "-sb", path], capture_output=True, timeout=10)
        return int(result.stdout.split()[0]) if result.returncode == 0 else 0
    except Exception:
        return 0


def _path_sizes(bench_root: Path, config: BenchConfig) -> list[dict]:
    from pilot.managers.database import MariaDBManager

    benches_dir = str(bench_root)
    mariadb_dir = str(MariaDBManager(config.mariadb).data_dir)
    return [
        {"label": "Benches", "path": benches_dir, "used_bytes": _directory_size(benches_dir)},
        {"label": "MariaDB", "path": mariadb_dir, "used_bytes": _directory_size(mariadb_dir)},
    ]


def _log_file_info(description: str, path: Path) -> dict:
    from datetime import datetime

    if path.exists():
        last_modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat(timespec="seconds")
    else:
        last_modified = None
    return {"description": description, "path": str(path), "last_modified": last_modified}


@stats_bp.get("/monitor/status")
def get_monitor_status():
    from pilot.config import MonitorConfig

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        config = BenchConfig.read(bench_root)
        mon = config.monitor
        log_path = mon.log_path or MonitorConfig.default_log_path(config.name)
        return jsonify(
            [
                _log_file_info("System Log", mon.system_log_path),
                _log_file_info("Application Log", log_path),
            ]
        )
    except Exception:
        return error_response(
            "monitor_status_unavailable",
            "Could not read monitor status.",
            500,
        )


@stats_bp.get("/monitor/history")
def get_monitor_history():
    from admin.backend.providers.monitor import MonitorProvider

    bench_root = Path(current_app.config["BENCH_ROOT"])
    window = request.args.get("window", "1h")
    try:
        return jsonify(MonitorProvider(bench_root, window).get_history())
    except Exception:
        return error_response(
            "monitor_history_unavailable",
            "Could not read monitor history.",
            500,
        )


@stats_bp.get("/waf")
def get_waf_analytics():
    from admin.backend.providers.waf import WafProvider

    bench_root = Path(current_app.config["BENCH_ROOT"])
    window = request.args.get("window", "24h")
    try:
        return jsonify(WafProvider(bench_root, window).get_analytics())
    except Exception:
        return error_response("waf_analytics_unavailable", "Could not read WAF analytics.", 500)


@stats_bp.get("/system")
def system_info():
    from admin.backend.providers.os import OSProvider
    from pilot.managers.platform import kernel_version, os_version

    bench_root = Path(current_app.config["BENCH_ROOT"])
    config = BenchConfig.read(bench_root)
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return jsonify(
        {
            "disk_total": psutil.disk_usage("/").total,
            "cpu_count": os.cpu_count(),
            "memory_total": mem.total,
            "swap_total": swap.total,
            "kernel_version": kernel_version(),
            "os_version": os_version(),
            "runtime": OSProvider(bench_root, config).get_versions(),
        }
    )


@stats_bp.get("/metrics")
def stats():
    bench_root = current_app.config["BENCH_ROOT"]
    config = BenchConfig.read(bench_root)
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    paths = _path_sizes(bench_root, config)
    return jsonify(
        {
            "cpu_percent": psutil.cpu_percent(),
            "cpu_count": os.cpu_count(),
            "cpu_breakdown": _cpu_breakdown(),
            "load_avg": os.getloadavg(),
            "memory_percent": mem.percent,
            "memory_used": mem.total - mem.available,
            "memory_total": mem.total,
            "memory_breakdown": _memory_breakdown(mem, swap),
            "disk_percent": disk.percent,
            "disk_used": disk.used,
            "disk_total": disk.total,
            **_io_rates(),
            "paths": paths,
        }
    )
