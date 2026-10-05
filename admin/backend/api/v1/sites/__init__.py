from flask import Blueprint

sites_bp = Blueprint("sites", __name__)


from admin.backend.api.v1.sites import (  # noqa: E402
    apps,
    backups,
    central,
    configuration,
    core,
    domains,
    frappe_cloud,
    monitoring,
    restore,
    storage,
    uploads,
    uptime,
)

__all__ = [
    "apps",
    "backups",
    "central",
    "configuration",
    "core",
    "domains",
    "frappe_cloud",
    "monitoring",
    "restore",
    "sites_bp",
    "storage",
    "uploads",
    "uptime",
]
