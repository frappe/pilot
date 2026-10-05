from __future__ import annotations

from pathlib import Path

from flask import current_app, jsonify, request

from admin.backend.api.responses import error_response
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import malformed_body, site_name, site_not_found
from admin.backend.api.v1.types.site_backups import BackupUploadFile, BackupUploadStarted, BackupUploadStatus
from admin.backend.middleware import require_scope
from pilot.core.bench import Bench
from pilot.core.bench.uploads import CHUNK_SIZE, BackupUpload
from pilot.core.site.restore import RESTORE_PARTS
from pilot.exceptions import BenchError, UploadOffsetError
from pilot.internal.site_paths import site_exists


@sites_bp.post("/<name>/uploads")
@require_scope(site_name)
def start_backup_upload(name: str):
    """Start a chunked upload of backup files: `{"files": {part: {"filename", "size"}}}`."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return malformed_body()
    files = _files(data.get("files"))
    if files is None:
        return _invalid(f"Describe each file by part ({', '.join(RESTORE_PARTS)}) with its filename and size.")
    try:
        upload = BackupUpload.start(Bench(bench_root), name, files)
    except BenchError as error:
        return _invalid(str(error))
    return jsonify(BackupUploadStarted(upload_id=upload.upload_id, chunk_size=CHUNK_SIZE)), 201


@sites_bp.put("/<name>/uploads/<upload_id>/files/<part>")
@require_scope(site_name)
def write_backup_upload_chunk(name: str, upload_id: str, part: str):
    """Raw chunk bytes for `part` at `?offset=`. Resend a chunk to retry it."""
    offset = request.args.get("offset", type=int)
    if offset is None or request.content_length is None:
        return _invalid("Send the chunk with its offset and Content-Length.")
    return _with_upload(name, upload_id, lambda upload: {"received": upload.write_chunk(part, offset, request.stream, request.content_length)})


@sites_bp.get("/<name>/uploads/<upload_id>")
@require_scope(site_name)
def get_backup_upload(name: str, upload_id: str):
    def status(upload: BackupUpload) -> BackupUploadStatus:
        return BackupUploadStatus(files={part: BackupUploadFile(size=file["size"], received=file["received"]) for part, file in upload.files.items()})

    return _with_upload(name, upload_id, status)


@sites_bp.delete("/<name>/uploads/<upload_id>")
@require_scope(site_name)
def cancel_backup_upload(name: str, upload_id: str):
    return _with_upload(name, upload_id, lambda upload: upload.delete() or {})


def find_upload(bench_root: Path, name: str, upload_id: str) -> BackupUpload:
    """The site's own upload. Another site's upload is reported as missing."""
    upload = BackupUpload(Bench(bench_root), upload_id)
    if upload.site != name:
        raise BenchError("The upload does not exist.")
    return upload


def _with_upload(name: str, upload_id: str, action):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        return jsonify(action(find_upload(bench_root, name, upload_id)))
    except UploadOffsetError as error:
        return error_response("upload_offset", str(error), 409)
    except BenchError as error:
        return _invalid(str(error))


def _files(value) -> dict[str, dict] | None:
    if not isinstance(value, dict) or not value or any(part not in RESTORE_PARTS for part in value):
        return None
    for file in value.values():
        if not isinstance(file, dict) or not isinstance(file.get("filename"), str):
            return None
        if not isinstance(file.get("size"), int) or isinstance(file["size"], bool) or file["size"] <= 0:
            return None
    return {part: {"filename": file["filename"], "size": file["size"]} for part, file in value.items()}


def _invalid(message: str):
    return error_response("invalid_upload", message, 422)
