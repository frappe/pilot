from __future__ import annotations

from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from pilot.managers.snapshot_orchestrator import get_orchestrator

from ..readers.snapshot_reader import SnapshotReader
from ..readers.volume_reader import VolumeReader
from ..validators import validate_cron_expression

volume_bp = Blueprint("volume", __name__)

# Not a legal site name (site names allow only letters/numbers/hyphens/dots),
# so this can never collide with a per-site backup job key in the same crontab.
_SNAPSHOT_CRON_JOB_KEY = "__bench_snapshot__"


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
    limit = request.args.get("limit", type=int)
    try:
        status = SnapshotReader(bench_root).read(limit=limit)
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
                    "is_offsite": s.is_offsite,
                    "is_local": s.is_local,
                    "is_uploading": s.is_uploading,
                    "is_downloaded": s.is_downloaded,
                }
                for s in status.snapshots
            ],
        }
    )


@volume_bp.route("/snapshots", methods=["POST"])
def create_snapshot():
    from pilot.config.toml_store import BenchTomlStore
    from admin.backend.tasks.manager.task_runner import TaskRunner

    bench_root = current_app.config["BENCH_ROOT"]
    config = _get_config(bench_root)
    tag = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        orchestrator = get_orchestrator(bench_root)
        orchestrator.create_snapshot(tag)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    s3_config = BenchTomlStore.for_bench(bench_root).read().s3
    task_id = None
    if s3_config.is_configured:
        task_id = TaskRunner(bench_root).run("offsite-snapshot", {"dataset": config.dataset_path, "tag": tag})

    return jsonify({"ok": True, "tag": tag, "snapshots": [f"{config.dataset_path}@{tag}"], "task_id": task_id})


@volume_bp.route("/snapshots/<tag>/rollback", methods=["POST"])
def rollback_snapshot(tag: str):
    bench_root = current_app.config["BENCH_ROOT"]
    config = _get_config(bench_root)
    manager = _get_volume_manager(bench_root)
    is_local = any(snap.snapshot_tag == tag for snap in manager.list_snapshots(config.dataset_path))
    if not is_local:
        # CLI-only: `bench volume restore-snapshot <tag>`.
        return jsonify(
            {"error": "Offsite snapshots can only be restored from the CLI: bench volume restore-snapshot <tag>."},
        ), 400

    try:
        get_orchestrator(bench_root).rollback_snapshot(tag)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True})


@volume_bp.route("/snapshots/<tag>", methods=["DELETE"])
def destroy_snapshot(tag: str):
    from pilot.config.toml_store import BenchTomlStore
    from pilot.integrations.s3.snapshots import OffsiteSnapshot

    bench_root = current_app.config["BENCH_ROOT"]
    bench_config = BenchTomlStore.for_bench(bench_root).read()
    dataset = bench_config.volume.dataset_path
    try:
        manager = _get_volume_manager(bench_root)
        # A remote-only snapshot (already offloaded, local copy destroyed by
        # OffsiteSnapshotTask) has nothing to destroy locally.
        if any(snap.snapshot_tag == tag for snap in manager.list_snapshots(dataset)):
            manager.destroy_snapshot(dataset, tag)

        if bench_config.s3.is_configured:
            OffsiteSnapshot.from_config(bench_config.s3).delete(bench_config.name, tag)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True})


def _snapshot_cron_command(bench_root) -> str:
    from pilot.loader import cli_root

    bench_script = cli_root() / "bench"
    log_file = bench_root / "logs" / "snapshot.log"
    return f"{bench_script} -b {bench_root.name} volume snapshot >> {log_file} 2>&1"


@volume_bp.route("/snapshot-schedule")
def get_snapshot_schedule():
    from ..cron_manager import CronManager

    bench_root = current_app.config["BENCH_ROOT"]
    try:
        schedule = CronManager(bench_root).get_schedule(_SNAPSHOT_CRON_JOB_KEY)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"schedule": schedule})


@volume_bp.route("/snapshot-schedule", methods=["POST"])
def set_snapshot_schedule():
    from ..cron_manager import CronManager

    bench_root = current_app.config["BENCH_ROOT"]
    data = request.get_json(silent=True) or {}
    schedule = (data.get("schedule") or "").strip()
    err = validate_cron_expression(schedule)
    if err:
        return jsonify({"ok": False, "error": err})
    try:
        CronManager(bench_root).set_schedule(_SNAPSHOT_CRON_JOB_KEY, schedule, _snapshot_cron_command(bench_root))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True})


@volume_bp.route("/snapshot-schedule", methods=["DELETE"])
def delete_snapshot_schedule():
    from ..cron_manager import CronManager

    bench_root = current_app.config["BENCH_ROOT"]
    try:
        CronManager(bench_root).remove_schedule(_SNAPSHOT_CRON_JOB_KEY)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True})
