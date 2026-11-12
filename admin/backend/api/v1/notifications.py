from __future__ import annotations

from dataclasses import asdict
from typing import TypedDict

from flask import Blueprint, jsonify, request

from admin.backend.api.requests import ApiRequest
from admin.backend.api.responses import (
    PageMeta,
    error_response,
    no_content_response,
    paginated_response,
    parse_pagination,
)
from admin.backend.api.v1.settings.config import ConfigPatcher
from pilot.core.notification import CATEGORIES, Notification, NotificationStore
from pilot.core.notification.webhooks import ping_webhook


class NotificationPageMeta(PageMeta):
    unread: int


class NotificationPage(TypedDict):
    data: list[Notification]
    meta: NotificationPageMeta


notifications_bp = Blueprint("notifications", __name__)


def _store() -> NotificationStore:
    """The notification store."""
    return NotificationStore(ApiRequest.get_bench_root() / "logs")


@notifications_bp.get("/notifications")
def list_notifications():
    """Return the bench notification feed, newest first, with the unread count.

    The count rides along on every page so the sidebar badge and the feed can never
    disagree about what is unread."""
    limit, offset = parse_pagination(20, 100)
    category = request.args.get("category") or None
    if category and category not in CATEGORIES:
        return error_response("invalid_category", f"Unknown category: {category}.", 400)

    unread_only = request.args.get("unread_only") in ("1", "true", "True")
    try:
        store = _store()

        def fetch_newest(count: int) -> list:
            """The newest notifications that match the filters."""
            return [asdict(item) for item in store.list(count, category=category, unread_only=unread_only)]

        return paginated_response(fetch_newest, limit, offset, {"unread": store.unread_count})
    except Exception:
        return error_response("notifications_unavailable", "Could not read notifications.", 500)


@notifications_bp.post("/notifications/<name>/read")
def mark_notification_read(name: str):
    """Mark one notification as read."""
    try:
        _store().mark_read(name)
    except Exception:
        return error_response("notification_update_failed", "Could not mark the notification read.", 500)
    return no_content_response()


@notifications_bp.post("/notifications/read-all")
def mark_all_notifications_read():
    """Mark every notification as read."""
    try:
        _store().mark_all_read()
    except Exception:
        return error_response("notification_update_failed", "Could not mark notifications read.", 500)
    return no_content_response()


@notifications_bp.post("/notifications/webhooks/test")
def test_webhook():
    """Send a sample `ping` event to one webhook and return the receiver's answer.

    Body: `{"url", "token", "original_url"}` as in the settings form. A blank token uses the one stored for `original_url`."""
    entry = ApiRequest.get_json_body()
    bench = ApiRequest.get_bench()
    endpoints = ConfigPatcher.get_webhook_endpoints([entry], bench.config.webhook_endpoints)
    if not endpoints:
        return error_response("invalid_fields", "Enter a webhook URL.", 422)
    try:
        endpoints[0].validate()
        delivery = ping_webhook(bench, endpoints[0])
    except ValueError as error:
        return error_response("invalid_fields", str(error), 422)
    except OSError as error:
        return error_response("webhook_unreachable", f"Could not reach the webhook: {error}", 502)
    return jsonify({"status": delivery.status, "body": delivery.body, "is_success": delivery.is_success})
