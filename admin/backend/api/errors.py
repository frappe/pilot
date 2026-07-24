from __future__ import annotations

from flask import current_app, request
from werkzeug.exceptions import HTTPException, InternalServerError

from admin.backend.api.responses import error_response
from admin.backend.api.routes import is_api_path
from pilot.exceptions import (
    ConfigError,
    MigrationConflictError,
    TaskConflictError,
    TaskNotFoundError,
    TaskNotRunningError,
)

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


def install_api_error_handlers(app) -> None:
    app.register_error_handler(ApiProblem, _handle_api_problem)
    app.register_error_handler(TaskNotFoundError, _handle_task_not_found)
    app.register_error_handler(TaskNotRunningError, _handle_task_not_active)
    app.register_error_handler(TaskConflictError, _handle_task_conflict)
    app.register_error_handler(MigrationConflictError, _handle_migration_conflict)
    app.register_error_handler(ConfigError, _handle_config_unavailable)
    app.register_error_handler(405, _handle_method_not_allowed)
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


def _handle_migration_conflict(error: MigrationConflictError):
    return error_response("migration_conflict", str(error), 409)


def _handle_method_not_allowed(error: HTTPException):
    if not is_api_path(request.path):
        return error
    return error_response("method_not_allowed", "Method not allowed.", 405)


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
    message = "Request payload is too large." if status == 413 else error.description or "Request failed."
    return error_response(_HTTP_ERROR_CODES.get(status, "http_error"), message, status)


def _handle_unexpected_error(error: Exception):
    current_app.logger.error(
        "Unhandled API error",
        exc_info=(type(error), error, error.__traceback__),
    )
    if not is_api_path(request.path):
        return InternalServerError()
    return error_response("internal_error", "An internal error occurred.", 500)
