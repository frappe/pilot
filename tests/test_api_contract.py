from __future__ import annotations

from pathlib import Path

from admin.backend.api_contract import API_ROOT_PREFIX, API_V1_PREFIX
from admin.backend.app import create_app


def test_api_prefixes_define_one_version_boundary() -> None:
    assert API_ROOT_PREFIX == "/api"
    assert API_V1_PREFIX == "/api/v1"


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
    response = create_app(tmp_path).test_client().post("/api/v1/ping")

    assert response.status_code == 405
    assert response.content_type == "application/json"
    assert response.get_json() == {
        "error": {
            "code": "method_not_allowed",
            "message": "Method not allowed.",
            "details": {},
        }
    }


def test_unversioned_product_route_is_not_an_alias(tmp_path: Path) -> None:
    response = create_app(tmp_path).test_client().get("/api/status")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"
