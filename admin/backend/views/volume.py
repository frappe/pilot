from __future__ import annotations

from datetime import datetime

from flask import Blueprint, current_app, jsonify

from pilot.managers.snapshot_orchestrator import get_orchestrator

from ..readers.snapshot_reader import SnapshotReader
from ..readers.volume_reader import VolumeReader

volume_bp = Blueprint("volume", __name__)


def _get_config(bench_root):
    from pilot.config.toml_store import BenchTomlStore

    return BenchTomlStore.for_bench(bench_root).read().volume


def _get_volume_manager(bench_root):
    from pilot.config.toml_store import BenchTomlStore
    from pilot.managers.volume_manager import VolumeManager

    bench_config = BenchTomlStore.for_bench(bench_root).read()
    return VolumeManager(bench_config.volume)


@volume_bp.route("/status")
def status():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        config = _get_config(bench_root)
        info = VolumeReader(bench_root).read()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not info.enabled:
        return jsonify({"enabled": False, "snapshots_enabled": False})

    return jsonify(
        {
            "enabled": True,
            "snapshots_enabled": True,
            "pool": info.pool,
            "pool_health": info.pool_health,
            "datasets": [
                {
                    "name": d.name,
                    "used_bytes": d.used_bytes,
                    "available_bytes": d.available_bytes,
                    "quota_bytes": d.quota_bytes,
                    "reservation_bytes": d.reservation_bytes,
                }
                for d in info.datasets
            ],
        }
    )


@volume_bp.route("/snapshots")
def list_snapshots():
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        status = SnapshotReader(bench_root).read()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not status.volume_enabled:
        return jsonify({"error": "Volume management is disabled."}), 400

    return jsonify(
        {
            "snapshots_enabled": status.snapshots_enabled,
            "snapshots": [
                {
                    "dataset": s.dataset,
                    "tag": s.tag,
                    "created_at": s.created_at.isoformat(),
                    "used_bytes": s.used_bytes,
                }
                for s in status.snapshots
            ],
        }
    )


@volume_bp.route("/snapshots", methods=["POST"])
def create_snapshot():
    bench_root = current_app.config["BENCH_ROOT"]
    config = _get_config(bench_root)
    tag = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        orchestrator = get_orchestrator(bench_root)
        orchestrator.create_snapshot(tag)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, "tag": tag, "snapshots": [f"{config.dataset_path}@{tag}"]})


@volume_bp.route("/snapshots/<tag>/rollback", methods=["POST"])
def rollback_snapshot(tag: str):
    bench_root = current_app.config["BENCH_ROOT"]
    try:
        orchestrator = get_orchestrator(bench_root)
        orchestrator.rollback_snapshot(tag)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True})


@volume_bp.route("/snapshots/<tag>", methods=["DELETE"])
def destroy_snapshot(tag: str):
    bench_root = current_app.config["BENCH_ROOT"]
    config = _get_config(bench_root)
    try:
        manager = _get_volume_manager(bench_root)
        manager.destroy_snapshot(config.dataset_path, tag)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True})
