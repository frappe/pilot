from __future__ import annotations

from pathlib import Path

from flask import request

from admin.backend.api_contract import (
    API_ROOT_PREFIX,
    API_V1_PREFIX,
    ApiProblem,
    accepted_response,
    created_response,
    no_content_response,
)
from admin.backend.app import create_app
from admin.backend.auth import allow_unauthenticated
from pilot.exceptions import (
    ConfigError,
    TaskConflictError,
    TaskNotFoundError,
    TaskNotRunningError,
)


def test_api_prefixes_define_one_version_boundary() -> None:
    assert API_ROOT_PREFIX == "/api"
    assert API_V1_PREFIX == "/api/v1"


def test_resource_response_helpers_define_creation_and_deletion_contracts(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path)
    with app.test_request_context():
        created = created_response({"id": "one"}, "/api/v1/resources/one")
        accepted = accepted_response({"id": "task-one"}, "/api/v1/tasks/task-one")
        deleted = no_content_response()

    assert (created.status_code, created.headers["Location"], created.get_json()) == (
        201,
        "/api/v1/resources/one",
        {"id": "one"},
    )
    assert (accepted.status_code, accepted.headers["Location"], accepted.get_json()) == (
        202,
        "/api/v1/tasks/task-one",
        {"id": "task-one"},
    )
    assert deleted.status_code == 204
    assert deleted.get_data() == b""


def test_health_is_an_open_liveness_check(tmp_path: Path) -> None:
    response = create_app(tmp_path).test_client().get("/api/v1/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert response.headers["Access-Control-Allow-Origin"] == "*"


def test_unknown_api_route_returns_json_404(tmp_path: Path) -> None:
    response = create_app(tmp_path).test_client().get("/api/v1/not-a-route")

    assert response.status_code == 404
    assert response.content_type == "application/json"
    assert response.get_json() == {
        "error": {
            "code": "not_found",
            "message": "API route not found.",
            "details": {},
        }
    }


def test_wrong_api_method_returns_json_405(tmp_path: Path) -> None:
    response = create_app(tmp_path).test_client().post("/api/v1/health")

    assert response.status_code == 405
    assert response.content_type == "application/json"
    assert response.get_json() == {
        "error": {
            "code": "method_not_allowed",
            "message": "Method not allowed.",
            "details": {},
        }
    }


def test_api_problem_uses_the_canonical_error_shape(tmp_path: Path) -> None:
    app = create_app(tmp_path)

    @app.get("/api/v1/test-problem")
    @allow_unauthenticated
    def test_problem():
        raise ApiProblem("invalid_example", "Example is invalid.", 422, {"field": "name"})

    response = app.test_client().get("/api/v1/test-problem")

    assert response.status_code == 422
    assert response.get_json() == {
        "error": {
            "code": "invalid_example",
            "details": {"field": "name"},
            "message": "Example is invalid.",
        }
    }


def test_domain_errors_have_stable_public_mappings(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    errors = {
        "missing": TaskNotFoundError("Task not found: task-one"),
        "inactive": TaskNotRunningError("Task is not running: task-one"),
        "conflict": TaskConflictError("Idempotency key conflict"),
        "config": ConfigError("secret parser detail"),
    }

    @app.get("/api/v1/test-domain-error/<kind>")
    @allow_unauthenticated
    def test_domain_error(kind: str):
        raise errors[kind]

    client = app.test_client()

    missing = client.get("/api/v1/test-domain-error/missing")
    inactive = client.get("/api/v1/test-domain-error/inactive")
    conflict = client.get("/api/v1/test-domain-error/conflict")
    config = client.get("/api/v1/test-domain-error/config")
    assert (missing.status_code, missing.get_json()["error"]["code"]) == (
        404,
        "task_not_found",
    )
    assert (conflict.status_code, conflict.get_json()["error"]["code"]) == (
        409,
        "task_conflict",
    )
    assert (inactive.status_code, inactive.get_json()["error"]["code"]) == (
        409,
        "task_not_active",
    )
    assert (config.status_code, config.get_json()["error"]) == (
        503,
        {
            "code": "configuration_unavailable",
            "details": {},
            "message": "Bench configuration is unavailable.",
        },
    )
    assert b"secret parser detail" not in config.data


def test_unhandled_api_errors_are_json_without_internal_details(tmp_path: Path) -> None:
    app = create_app(tmp_path)

    @app.get("/api/v1/test-crash")
    @allow_unauthenticated
    def test_crash():
        raise RuntimeError("secret internal detail")

    response = app.test_client().get("/api/v1/test-crash")

    assert response.status_code == 500
    assert response.get_json()["error"] == {
        "code": "internal_error",
        "details": {},
        "message": "An internal error occurred.",
    }
    assert b"secret internal detail" not in response.data


def test_payload_too_large_is_a_json_api_error(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    app.config["MAX_CONTENT_LENGTH"] = 8

    @app.post("/api/v1/test-upload")
    @allow_unauthenticated
    def test_upload():
        return request.get_data()

    response = app.test_client().post(
        "/api/v1/test-upload",
        data=b'{"password":"too long"}',
        content_type="application/json",
    )

    assert response.status_code == 413
    assert response.get_json() == {
        "error": {
            "code": "payload_too_large",
            "details": {},
            "message": "Request payload is too large.",
        }
    }


def test_non_api_config_errors_keep_the_html_error_boundary(tmp_path: Path) -> None:
    app = create_app(tmp_path)

    @app.get("/test-config-error")
    def test_config_error():
        raise ConfigError("secret parser detail")

    response = app.test_client().get("/test-config-error")

    assert response.status_code == 500
    assert response.content_type.startswith("text/html")
    assert b"secret parser detail" not in response.data


def test_unversioned_product_route_is_not_an_alias(tmp_path: Path) -> None:
    response = create_app(tmp_path).test_client().get("/api/bootstrap")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"
