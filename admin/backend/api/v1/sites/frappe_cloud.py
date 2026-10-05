from __future__ import annotations

from pathlib import Path

from flask import current_app, jsonify, request

from admin.backend.api.responses import error_response
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import malformed_body, site_name, site_not_found
from admin.backend.api.v1.types.site_backups import (
    FrappeCloudBackup,
    FrappeCloudBackupList,
    FrappeCloudConnection,
)
from admin.backend.middleware import is_bench_scoped, require_scope
from pilot.core.bench import Bench
from pilot.exceptions import FrappeCloudError
from pilot.internal.site_paths import site_exists


@sites_bp.post("/<name>/integrations/frappe-cloud")
@require_scope(site_name)
def connect_frappe_cloud(name: str):
    """Ask Frappe Cloud for access to the backups of the site at `remote_site`."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return malformed_body()
    remote_site = data.get("remote_site")
    if not isinstance(remote_site, str) or not remote_site.strip():
        return _invalid("Enter a domain of the site on Frappe Cloud.")
    return _call(name, lambda frappe_cloud: _connection(frappe_cloud.connect(remote_site.strip())))


@sites_bp.get("/<name>/integrations/frappe-cloud")
@require_scope(site_name)
def get_frappe_cloud_connection(name: str):
    return _call(name, lambda frappe_cloud: _connection(frappe_cloud.get_status()))


@sites_bp.delete("/<name>/integrations/frappe-cloud")
@require_scope(site_name)
def disconnect_frappe_cloud(name: str):
    """Revoke the request on Frappe Cloud and forget its token."""
    return _call(name, lambda frappe_cloud: frappe_cloud.disconnect() or {})


@sites_bp.get("/<name>/integrations/frappe-cloud/backups")
@require_scope(site_name)
def list_frappe_cloud_backups(name: str):
    start = request.args.get("start", 0, type=int)
    def backups(frappe_cloud) -> FrappeCloudBackupList:
        """`running_backup` lets a reloaded page follow a backup it started before."""
        client = frappe_cloud.client
        return FrappeCloudBackupList(
            running_backup=client.get_running_backup(),
            backups=[
                FrappeCloudBackup(name=backup["name"], created_at=backup["created_at"], size_bytes=backup["size"])
                for backup in client.get_backups(max(start, 0))
            ]
        )

    return _call(name, backups)


@sites_bp.post("/<name>/integrations/frappe-cloud/backups")
@require_scope(site_name)
def take_frappe_cloud_backup(name: str):
    return _call(name, lambda frappe_cloud: {"name": frappe_cloud.client.take_backup()})


@sites_bp.get("/<name>/integrations/frappe-cloud/backups/<backup>")
@require_scope(site_name)
def get_frappe_cloud_backup(name: str, backup: str):
    def status(frappe_cloud) -> dict:
        values = frappe_cloud.client.get_backup_status(backup)
        return {"name": backup, "status": values["status"], "job_url": values["job_url"]}

    return _call(name, status)


def _call(name: str, action):
    """Run `action` on the site's Frappe Cloud link. Frappe Cloud's own message explains a refusal."""
    if not is_bench_scoped():
        return error_response("forbidden", "Restoring from Frappe Cloud needs a bench session.", 403)
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        return jsonify(action(Bench(bench_root).site(name).frappe_cloud))
    except FrappeCloudError as error:
        return _invalid(str(error))


def _connection(values: dict) -> FrappeCloudConnection:
    return FrappeCloudConnection(
        status=values["status"],
        remote_site=values["remote_site"],
        approval_url=values["approval_url"],
        code=values["code"],
    )


def _invalid(message: str):
    return error_response("frappe_cloud_error", message, 422)
