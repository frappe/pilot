from __future__ import annotations

import base64
from collections.abc import Callable

from flask import current_app, jsonify, request
from werkzeug.exceptions import HTTPException, InternalServerError

from pilot.exceptions import (
    ConfigError,
    TaskConflictError,
    TaskNotFoundError,
    TaskNotRunningError,
)

API_ROOT_PREFIX = "/api"
API_V1_PREFIX = f"{API_ROOT_PREFIX}/v1"

_HTTP_ERROR_CODES = {
    400: "malformed_request",
    401: "authentication_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    429: "rate_limit_exceeded",
}


class ApiProblem(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def is_api_path(path: str) -> bool:
    return path == API_ROOT_PREFIX or path.startswith(f"{API_ROOT_PREFIX}/")


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


def _resource_response(resource: dict, status: int, location: str):
    response = jsonify(resource)
    response.status_code = status
    response.headers["Location"] = location
    return response


_MAX_PAGE_OFFSET = 10_000


def parse_pagination(default_limit: int, max_limit: int) -> tuple[int, int]:
    """Read ``limit``/``cursor`` query params for a growing collection.
    Invalid or out-of-range values fall back to safe defaults rather than
    erroring, since pagination inputs are advisory, not semantic."""
    try:
        limit = int(request.args.get("limit", default_limit))
    except (TypeError, ValueError):
        limit = default_limit
    limit = max(1, min(limit, max_limit))
    return limit, _decode_cursor(request.args.get("cursor"))


def paginated_response(fetch_newest: Callable[[int], list], limit: int, offset: int):
    """``fetch_newest(n)`` returns the newest ``n`` matching items. Fetches one
    past the requested page to tell whether a next cursor should be reported,
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


def install_api_error_handlers(app) -> None:
    app.register_error_handler(ApiProblem, _handle_api_problem)
    app.register_error_handler(TaskNotFoundError, _handle_task_not_found)
    app.register_error_handler(TaskNotRunningError, _handle_task_not_active)
    app.register_error_handler(TaskConflictError, _handle_task_conflict)
    app.register_error_handler(ConfigError, _handle_config_unavailable)
    app.register_error_handler(HTTPException, _handle_http_error)
    app.register_error_handler(Exception, _handle_unexpected_error)


def _handle_api_problem(error: ApiProblem):
    return error_response(error.code, error.message, error.status, error.details)


def _handle_task_not_found(error: TaskNotFoundError):
    return error_response("task_not_found", str(error), 404)


def _handle_task_not_active(error: TaskNotRunningError):
    return error_response("task_not_active", str(error), 409)


def _handle_task_conflict(error: TaskConflictError):
    return error_response("task_conflict", str(error), 409)


def _handle_config_unavailable(_error: ConfigError):
    if not is_api_path(request.path):
        return InternalServerError()
    return error_response(
        "configuration_unavailable",
        "Bench configuration is unavailable.",
        503,
    )


def _handle_http_error(error: HTTPException):
    if not is_api_path(request.path):
        return error
    status = error.code or 500
    message = (
        "Request payload is too large."
        if status == 413
        else error.description or "Request failed."
    )
    return error_response(_HTTP_ERROR_CODES.get(status, "http_error"), message, status)


def _handle_unexpected_error(error: Exception):
    current_app.logger.error(
        "Unhandled API error",
        exc_info=(type(error), error, error.__traceback__),
    )
    if not is_api_path(request.path):
        return InternalServerError()
    return error_response("internal_error", "An internal error occurred.", 500)
