from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TypedDict

from flask import Blueprint, current_app, request

from admin.backend.api.responses import (
    PageMeta,
    error_response,
    no_content_response,
    paginated_response,
    parse_pagination,
)
from pilot.core.notification import CATEGORIES, Notification, NotificationStore


class NotificationPageMeta(PageMeta):
    unread: int


class NotificationPage(TypedDict):
    data: list[Notification]
    meta: NotificationPageMeta


notifications_bp = Blueprint("notifications", __name__)

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


def _store() -> NotificationStore:
    return NotificationStore(Path(current_app.config["BENCH_ROOT"]) / "logs")


@notifications_bp.get("/notifications")
def list_notifications():
    """Return the bench notification feed, newest first, with the unread count.

    The count rides along on every page so the sidebar badge and the feed can never
    disagree about what is unread."""
    limit, offset = parse_pagination(_DEFAULT_LIMIT, _MAX_LIMIT)
    category = request.args.get("category") or None
    if category and category not in CATEGORIES:
        return error_response("invalid_category", f"Unknown category: {category}.", 400)

    unread_only = request.args.get("unread_only") in ("1", "true", "True")
    try:
        store = _store()

        def fetch_newest(count: int) -> list:
            return [asdict(item) for item in store.list(count, category=category, unread_only=unread_only)]

        return paginated_response(fetch_newest, limit, offset, {"unread": store.unread_count})
    except Exception:
        return error_response("notifications_unavailable", "Could not read notifications.", 500)


@notifications_bp.post("/notifications/<name>/read")
def mark_notification_read(name: str):
    try:
        _store().mark_read(name)
    except Exception:
        return error_response("notification_update_failed", "Could not mark the notification read.", 500)
    return no_content_response()


@notifications_bp.post("/notifications/read-all")
def mark_all_notifications_read():
    try:
        _store().mark_all_read()
    except Exception:
        return error_response("notification_update_failed", "Could not mark notifications read.", 500)
    return no_content_response()
