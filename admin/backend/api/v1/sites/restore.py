from __future__ import annotations

import re
import secrets
import shutil
from pathlib import Path

from flask import current_app, jsonify, request

from admin.backend.api.responses import accepted_task_response, error_response
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import malformed_body, site_name, site_not_found, task_failure
from admin.backend.api.v1.types.site_backups import RemoteBackup, RemoteBackupList
from admin.backend.middleware import is_bench_scoped, require_scope
from pilot.core.bench import Bench
from pilot.core.site.restore import RESTORE_PARTS
from pilot.internal.site_paths import site_exists
from pilot.tasks.restore_site import RestoreSiteTask
from pilot.utils import make_private_directory

_TIMESTAMP_RE = re.compile(r"^\d{8}_\d{6}$")
# Saved under names Frappe's backup files carry, so the restore can tell the parts apart.
_UPLOAD_NAMES = {
    "database": ("upload-database.sql", "upload-database.sql.gz"),
    "public": ("upload-files.tar", "upload-files.tgz"),
    "private": ("upload-private-files.tar", "upload-private-files.tgz"),
    "config": ("upload-site_config_backup.json", "upload-site_config_backup.json"),
}


@sites_bp.post("/<name>/actions/restore")
@require_scope(site_name)
def restore_site(name: str):
    """Restore from a site on this bench (a backup run or a fresh backup) or a remote site."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return malformed_body()
    parts = _parts(data.get("parts"))
    if parts is None:
        return _invalid(f"Choose what to restore: {', '.join(RESTORE_PARTS)}.")

    if data.get("remote_site"):
        return _restore_from_remote(bench_root, name, parts, data)
    return _restore_from_bench_site(bench_root, name, parts, data)


@sites_bp.post("/<name>/actions/restore-upload")
@require_scope(site_name)
def restore_site_from_upload(name: str):
    """Restore from backup files sent as multipart fields database, public, private and config."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    parts = _parts(request.form.getlist("parts"))
    if parts is None:
        return _invalid(f"Choose what to restore: {', '.join(RESTORE_PARTS)}.")
    if missing := [part for part in parts if part not in request.files]:
        return _invalid(f"Upload the {' and '.join(missing)} file.")

    # The task's cleanup-site-restore callback removes this directory if the restore fails.
    upload_dir = Bench(bench_root).uploads_path / secrets.token_hex(8)
    make_private_directory(upload_dir, parents=True)
    # The site config carries the encryption key a restored database needs.
    fields = [*parts, "config"] if "database" in parts else parts
    try:
        for field in fields:
            if upload := request.files.get(field):
                plain, compressed = _UPLOAD_NAMES[field]
                is_compressed = (upload.filename or "").endswith((".gz", ".tgz"))
                upload.save(upload_dir / (compressed if is_compressed else plain))
        response = current_app.make_response(
            _queue(bench_root, name, parts, {name}, upload_dir=str(upload_dir), idempotent=False)
        )
    except Exception:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise
    if response.status_code >= 400:
        shutil.rmtree(upload_dir, ignore_errors=True)
    return response


def _restore_from_bench_site(bench_root: Path, name: str, parts: list[str], data: dict):
    source = data.get("source_site") or name
    timestamp = data.get("backup_timestamp") or ""
    # A site token may read its own backups only. Checked first, so it cannot probe for sites.
    if source != name and not is_bench_scoped():
        return error_response("forbidden", "Restoring from another site needs a bench session.", 403)
    if not isinstance(source, str) or not site_exists(bench_root, source):
        return site_not_found()
    if not isinstance(timestamp, str) or (timestamp and not _TIMESTAMP_RE.fullmatch(timestamp)):
        return _invalid("The backup timestamp is not valid.")
    return _queue(bench_root, name, parts, {name, source}, source_site=source, backup_timestamp=timestamp)


@sites_bp.post("/<name>/actions/remote-backups")
@require_scope(site_name)
def list_remote_backups(name: str):
    """The latest backup of a remote Frappe site, which is what a remote restore uses."""
    from admin.backend.providers.backups import BackupProvider
    from pilot.exceptions import RemoteSiteError

    if not site_exists(Path(current_app.config["BENCH_ROOT"]), name):
        return site_not_found()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return malformed_body()
    remote, failure = _signed_in_remote(data)
    if failure:
        return failure
    try:
        timestamp, files = remote.get_latest_run()
    except RemoteSiteError as error:
        return _invalid(str(error))
    backups: list[RemoteBackup] = []
    if created_at := BackupProvider.get_timestamp(timestamp):
        parts = [part for part in RESTORE_PARTS if part in files]
        backups.append(RemoteBackup(timestamp=timestamp, created_at=created_at.isoformat(), parts=parts))
    return jsonify(RemoteBackupList(backups=backups))


def _restore_from_remote(bench_root: Path, name: str, parts: list[str], data: dict):
    timestamp = data.get("backup_timestamp")
    if not isinstance(timestamp, str) or not _TIMESTAMP_RE.fullmatch(timestamp):
        return _invalid("Get the backups of the remote site first.")
    _, failure = _signed_in_remote(data)
    if failure:
        return failure
    source = {"remote_site": data["remote_site"], "remote_password": data["password"], "backup_timestamp": timestamp}
    return _queue(bench_root, name, parts, {name}, **source)


def _signed_in_remote(data: dict):
    """The remote site signed in as Administrator, or the response that explains why not."""
    from pilot.exceptions import RemoteSiteError
    from pilot.integrations.frappe_site import RemoteFrappeSite

    # The admin calls out to the given host, so a site token must not choose one.
    if not is_bench_scoped():
        return None, error_response("forbidden", "Restoring from a remote site needs a bench session.", 403)
    remote_site, password = data.get("remote_site"), data.get("password")
    if not isinstance(remote_site, str) or not isinstance(password, str) or not password:
        return None, _invalid("Enter the site and its Administrator password.")
    if remote_site.strip().startswith("http://"):
        return None, _invalid("Use an https:// address.")
    remote = RemoteFrappeSite(remote_site, password)
    try:
        remote.login()
    except RemoteSiteError as error:
        return None, _invalid(str(error))
    return remote, None


def _queue(bench_root: Path, name: str, parts: list[str], sites: set[str], idempotent: bool = True, **source):
    """`idempotent` is off for uploads: a replayed key would leave the new upload unused."""
    try:
        task_id = RestoreSiteTask.queue(
            Bench(bench_root),
            site=name,
            parts=parts,
            idempotency_key=request.headers.get("Idempotency-Key") if idempotent else None,
            resource_key=[f"site:{site.lower()}" for site in sorted(sites)],
            **source,
        )
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)


def _parts(value) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    if any(not isinstance(part, str) or part not in RESTORE_PARTS for part in value):
        return None
    return [part for part in RESTORE_PARTS if part in value]


def _invalid(message: str):
    return error_response("invalid_restore", message, 422)
