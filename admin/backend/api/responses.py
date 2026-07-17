from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path

from flask import current_app, jsonify, request, url_for

from pilot.tasks.manager.task_reader import TaskReader

_MAX_PAGE_OFFSET = 10_000


def error_response(
    code: str,
    message: str,
    status: int,
    details: dict | None = None,
):
    return (
        jsonify(
            {
                "error": {
                    "code": code,
                    "message": message,
                    "details": details or {},
                }
            }
        ),
        status,
    )


def created_response(resource: dict, location: str):
    return _resource_response(resource, 201, location)


def accepted_response(resource: dict, location: str):
    return _resource_response(resource, 202, location)


def no_content_response():
    return current_app.response_class(status=204)


def accepted_task_response(bench_root: Path, task_id: str):
    task = TaskReader(bench_root).read_task(task_id)
    return accepted_response(
        task.as_dict(),
        url_for("tasks.get_task", task_id=task_id),
    )


def _resource_response(resource: dict, status: int, location: str):
    response = jsonify(resource)
    response.status_code = status
    response.headers["Location"] = location
    return response


def parse_pagination(default_limit: int, max_limit: int) -> tuple[int, int]:
    """Read limit/cursor query params for a growing collection. Invalid or
    out-of-range values fall back to safe defaults rather than erroring,
    since pagination inputs are advisory, not semantic."""
    try:
        limit = int(request.args.get("limit", default_limit))
    except (TypeError, ValueError):
        limit = default_limit
    limit = max(1, min(limit, max_limit))
    return limit, _decode_cursor(request.args.get("cursor"))


def paginated_response(fetch_newest: Callable[[int], list], limit: int, offset: int):
    """fetch_newest(n) returns the newest n matching items. Fetches one past
    the requested page to tell whether a next cursor should be reported,
    without requiring the caller to know the collection's total size."""
    fetched = fetch_newest(min(offset + limit + 1, _MAX_PAGE_OFFSET + limit + 1))
    page = fetched[offset : offset + limit]
    next_cursor = _encode_cursor(offset + limit) if len(fetched) > offset + limit else None
    return jsonify({"data": page, "meta": {"limit": limit, "next_cursor": next_cursor}})


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        offset = int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception:
        return 0
    return offset if 0 <= offset <= _MAX_PAGE_OFFSET else 0
