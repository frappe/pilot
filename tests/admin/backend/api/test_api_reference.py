from __future__ import annotations

from pathlib import Path

from admin.backend.api.reference import get_api_reference

REFERENCE = Path(__file__).parents[4] / "docs" / "admin-api-reference.md"


def test_reference_lists_the_current_routes():
    assert REFERENCE.read_text(encoding="utf-8") == get_api_reference(), (
        "docs/admin-api-reference.md is out of date. Run `pilot admin api-reference`."
    )
