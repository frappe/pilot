from __future__ import annotations

from pathlib import Path

from flask import current_app, jsonify, request, send_file

from admin.backend.api.responses import accepted_task_response, error_response, no_content_response
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import (
    internal_error,
    invalid_fields,
    malformed_body,
    site_name,
    site_not_found,
    task_failure,
    text_fields,
)
from admin.backend.middleware import require_scope
from pilot.core.bench import Bench
from pilot.exceptions import BenchError
from pilot.internal.site_paths import site_exists
from pilot.internal.validators import validate_cron_expression
from pilot.tasks.backup_site import BackupSiteTask

_DEFAULT_BACKUPS_PAGE_SIZE = 20


@sites_bp.post("/<name>/backups")
@require_scope(site_name)
def backup_site(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        task_id = BackupSiteTask.queue(Bench(bench_root), site=name, with_files=True)
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
    try:
        target = Bench(bench_root).site(name).backups.download_file_path(timestamp, file_id)
    except BenchError:
        return error_response("invalid_filename", "Backup filename is invalid.", 422)
    except Exception:
        return error_response("backup_not_found", "Backup file not found.", 404)

    return send_file(target, as_attachment=True, download_name=file_id)


def _site_backups_dir(bench_root: Path, name: str) -> Path:
    return Bench(bench_root).site(name).backups.directory


@sites_bp.get("/<name>/backups/<timestamp>/download-links")
@require_scope(site_name)
def backup_download_links(name: str, timestamp: str):
    """Pre-signed S3 URLs for a backup run's files - the user downloads
    straight from the bucket, so this server never proxies the transfer."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        links = Bench(bench_root).site(name).backups.download_links(timestamp)
    except FileNotFoundError:
        return error_response("backup_not_found", "Offsite backup not found.", 404)
    except Exception:
        return internal_error("Could not create offsite backup URLs.")

    return jsonify(links)


def _backup_cron_command(bench_root: Path, site: str) -> str:
    return Bench(bench_root).site(site).backups._cron_command()


def _retention_from_payload(block: dict | None):
    from pilot.core.site.backups import retention_from_payload

    return retention_from_payload(block)


@sites_bp.get("/<name>/backup-schedule")
@require_scope(site_name)
def get_backup_schedule(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        schedule = Bench(bench_root).site(name).backups.schedule()
    except Exception:
        return internal_error("Could not read the backup schedule.")
    return jsonify(schedule)


@sites_bp.put("/<name>/backup-schedule")
@require_scope(site_name)
def set_backup_schedule(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return malformed_body()
    fields = text_fields(data, "schedule")
    retention_value = data.get("retention")
    if fields is None or (retention_value is not None and not isinstance(retention_value, dict)):
        return invalid_fields()
    schedule = fields["schedule"]
    if err := validate_cron_expression(schedule):
        return error_response("invalid_schedule", err, 422)
    backups = Bench(bench_root).site(name).backups
    retention = backups.retention_from_payload(retention_value)
    if isinstance(retention, str):
        return error_response("invalid_retention", retention, 422)
    try:
        saved = backups.set_schedule(schedule, retention)
    except Exception:
        return internal_error("Could not update the backup schedule.")
    return jsonify(saved)


@sites_bp.delete("/<name>/backup-schedule")
@require_scope(site_name)
def delete_backup_schedule(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        Bench(bench_root).site(name).backups.clear_schedule()
    except Exception:
        return internal_error("Could not remove the backup schedule.")
    return no_content_response()
