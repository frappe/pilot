from __future__ import annotations

from pathlib import Path

from flask import current_app, jsonify, request, send_file

from pilot.internal.site_paths import site_exists
from pilot.internal.validators import validate_cron_expression
from pilot.tasks.manager.task_runner import TaskRunner

from admin.backend.api.responses import accepted_task_response, error_response, no_content_response
from admin.backend.middleware import require_scope

from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import internal_error, invalid_fields, malformed_body, site_name, site_not_found, task_failure, text_fields

_DEFAULT_BACKUPS_PAGE_SIZE = 20


@sites_bp.post("/<name>/backups")
@require_scope(site_name)
def backup_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        task_id = TaskRunner(bench_root).run("backup-site", {"site": name, "with_files": True})
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)


@sites_bp.get("/<name>/backups")
@require_scope(site_name)
def list_backups(name: str):
    from admin.backend.providers.backups import BackupProvider

    bench_root = Path(current_app.config["BENCH_ROOT"])
    limit = request.args.get("limit", _DEFAULT_BACKUPS_PAGE_SIZE, type=int)
    try:
        sets = BackupProvider(bench_root, name).get_all(limit=limit)
    except Exception:
        return internal_error("Could not read site backups.")
    return jsonify([_backup_set_resource(s) for s in sets])


@sites_bp.get("/<name>/backups/<timestamp>")
@require_scope(site_name)
def get_backup(name: str, timestamp: str):
    from admin.backend.providers.backups import BackupProvider

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        sets = BackupProvider(bench_root, name).get_all()
    except Exception:
        return internal_error("Could not read site backups.")
    match = next((s for s in sets if s.timestamp == timestamp), None)
    if match is None:
        return error_response("backup_not_found", "Backup not found.", 404)
    return jsonify(_backup_set_resource(match))


def _backup_set_resource(s) -> dict:
    return {
        "timestamp": s.timestamp,
        "created_at": s.created_at.isoformat(),
        "is_offsite": s.is_offsite,
        "files": [
            {
                "filename": f.filename,
                "path": f.path,
                "size_bytes": f.size_bytes,
                "kind": f.kind,
            }
            for f in s.files
        ],
    }


@sites_bp.get("/<name>/backups/<timestamp>/files/<file_id>/content")
@require_scope(site_name)
def download_backup_file(name: str, timestamp: str, file_id: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not file_id.startswith(timestamp) or "/" in file_id or "\\" in file_id or file_id.startswith("."):
        return error_response("invalid_filename", "Backup filename is invalid.", 422)

    backups_dir = (bench_root / "sites" / name / "private" / "backups").resolve()
    target = (backups_dir / file_id).resolve()
    if backups_dir not in target.parents or not target.is_file():
        return error_response("backup_not_found", "Backup file not found.", 404)

    return send_file(target, as_attachment=True, download_name=file_id)


@sites_bp.get("/<name>/backups/<timestamp>/download-links")
@require_scope(site_name)
def backup_download_links(name: str, timestamp: str):
    """Pre-signed S3 URLs for a backup run's files — the user downloads
    straight from the bucket, so this server never proxies the transfer."""
    from pilot.config.toml_store import BenchTomlStore
    from pilot.integrations.s3.backups import OffsiteBackup

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        config = BenchTomlStore.for_bench(bench_root).read()
        offsite_backup = OffsiteBackup.from_config(config.s3, bench_root)
        files = offsite_backup.get_backup(name, timestamp)
        if not files:
            return error_response(
                "backup_not_found", "Offsite backup not found.", 404
            )
        links = {
            kind: offsite_backup.presigned_url(name, timestamp, filename)
            for kind, filename in files.items()
        }
    except Exception:
        return internal_error("Could not create offsite backup URLs.")

    return jsonify(links)


def _backup_cron_command(bench_root: Path, site: str) -> str:
    import sys

    log_file = bench_root / "logs" / f"backup-{site}.log"
    return f"{sys.executable} -m pilot.tasks.jobs.backup_site_task {bench_root} {site} --with-files >> {log_file} 2>&1"


def _retention_from_payload(block: dict | None):
    """Build a validated BackupConfig from the UI payload, defaulting to GFS.
    Returns the config, or an error string."""
    from pilot.config.backup_config import VALID_SCHEMES, BackupConfig

    block = block or {}
    config = BackupConfig()
    scheme = str(block.get("scheme", config.scheme)).strip()
    if scheme not in VALID_SCHEMES:
        return f"Retention scheme must be one of: {', '.join(VALID_SCHEMES)}."
    config.scheme = scheme
    for key in config.counts:
        if key not in block:
            continue
        try:
            value = int(block[key])
        except (TypeError, ValueError):
            return f"{key} must be a whole number."
        if value < 0:
            return f"{key} must be zero or more."
        setattr(config, key, value)
    return config


@sites_bp.get("/<name>/backup-schedule")
@require_scope(site_name)
def get_backup_schedule(name: str):
    from dataclasses import asdict

    from pilot.config.site_backup_config import read_retention
    from pilot.managers.cron import CronManager

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        schedule = CronManager(bench_root).get_schedule(name)
        retention = read_retention(bench_root / "sites" / name / "site_config.json")
    except Exception:
        return internal_error("Could not read the backup schedule.")
    return jsonify({"schedule": schedule, "retention": asdict(retention) if retention else None})


@sites_bp.put("/<name>/backup-schedule")
@require_scope(site_name)
def set_backup_schedule(name: str):
    from pilot.config.site_backup_config import write_retention
    from pilot.managers.cron import CronManager

    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return malformed_body()
    fields = text_fields(data, "schedule")
    retention_value = data.get("retention")
    if fields is None or (
        retention_value is not None and not isinstance(retention_value, dict)
    ):
        return invalid_fields()
    schedule = fields["schedule"]
    if err := validate_cron_expression(schedule):
        return error_response("invalid_schedule", err, 422)
    retention = _retention_from_payload(retention_value)
    if isinstance(retention, str):
        return error_response("invalid_retention", retention, 422)
    try:
        CronManager(bench_root).set_schedule(name, schedule, _backup_cron_command(bench_root, name))
        write_retention(bench_root / "sites" / name / "site_config.json", retention)
    except Exception:
        return internal_error("Could not update the backup schedule.")
    return get_backup_schedule(name)


@sites_bp.delete("/<name>/backup-schedule")
@require_scope(site_name)
def delete_backup_schedule(name: str):
    from pilot.config.site_backup_config import clear_retention
    from pilot.managers.cron import CronManager

    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        CronManager(bench_root).remove_schedule(name)
        clear_retention(bench_root / "sites" / name / "site_config.json")
    except Exception:
        return internal_error("Could not remove the backup schedule.")
    return no_content_response()
