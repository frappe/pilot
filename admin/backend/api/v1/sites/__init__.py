from flask import Blueprint

sites_bp = Blueprint("sites", __name__)

from admin.backend.api.v1.sites import apps, backups, configuration, core, domains  # noqa: E402

__all__ = [
    "apps",
    "backups",
    "configuration",
    "core",
    "domains",
    "sites_bp",
]
