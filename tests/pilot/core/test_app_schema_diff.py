from __future__ import annotations

import json

import pytest

from pilot.core.app.schema_diff import get_schema_migration_kind


@pytest.mark.parametrize("path", [
    "erpnext/sidebar/selling/selling.json",
    "erpnext/dock/main/main.json",
    "erpnext/selling/doctype/customer/list_filter/current.json",
    "erpnext/selling/doctype/customer/kanban_board/customers.json",
    "erpnext/selling/doctype_settings_map/customer.json",
    "erpnext/selling/custom_sync_type/foo/foo.json",
])
def test_syncable_metadata_requires_maintenance(path):
    assert get_schema_migration_kind({path: "D"}, lambda _: None, lambda _: None) == "maintenance"

DOCTYPE_PATH = "demo/demo/doctype/task/task.json"
TITLE = {"fieldname": "title", "fieldtype": "Data", "label": "Title"}
PRIORITY = {"fieldname": "priority", "fieldtype": "Select", "options": "Low\nHigh"}
TASK = {
    "doctype": "DocType",
    "name": "Task",
    "module": "Demo",
    "field_order": ["title", "priority"],
    "fields": [TITLE, PRIORITY],
    "istable": 0,
    "modified": "2026-01-01 00:00:00",
}


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        pytest.param(
            {
                "fields": [TITLE, PRIORITY, {"fieldname": "notes", "fieldtype": "Text"}],
                "field_order": ["title", "priority", "notes"],
                "modified": "2026-02-01 00:00:00",
            },
            "online",
            id="added-field",
        ),
        pytest.param({"fields": [TITLE]}, "maintenance", id="removed-field"),
        pytest.param({"fields": [{**TITLE, "fieldtype": "Int"}, PRIORITY]}, "maintenance", id="fieldtype"),
        pytest.param({"fields": [{**TITLE, "label": "Summary"}, PRIORITY]}, "online", id="label"),
        pytest.param(
            {"fields": [TITLE, {**PRIORITY, "options": "High"}]}, "maintenance", id="select-options"
        ),
        pytest.param({"fields": [{**TITLE, "length": 100}, PRIORITY]}, "maintenance", id="field-length"),
        pytest.param({"fields": [{**TITLE, "reqd": 1}, PRIORITY]}, "maintenance", id="required-field"),
        pytest.param({"fields": [TITLE, TITLE]}, "maintenance", id="duplicate-fieldnames"),
        pytest.param({"fields": [{"fieldtype": "Data"}]}, "maintenance", id="missing-fieldname"),
        pytest.param({"istable": 1}, "maintenance", id="istable"),
        pytest.param({"istable": False}, "maintenance", id="istable-type"),
        pytest.param({"name": "Issue"}, "maintenance", id="renamed-doctype"),
        pytest.param({"module": "Other"}, "maintenance", id="changed-module"),
        pytest.param({"permissions": [{"role": "Guest", "read": 1}]}, "maintenance", id="permissions"),
    ],
)
def test_modified_doctype(updates: dict, expected: str) -> None:
    assert (
        get_schema_migration_kind(
            {DOCTYPE_PATH: "M"},
            {DOCTYPE_PATH: json.dumps(TASK)}.get,
            {DOCTYPE_PATH: json.dumps({**TASK, **updates})}.get,
        )
        == expected
    )


@pytest.mark.parametrize(
    "attributes",
    [
        {"reqd": 1}, {"reqd": 1, "default": "TBD"}, {"unique": 1}, {"search_index": 1},
        {"not_nullable": 1}, {"default": "TBD"}, {"fieldtype": "Check"},
    ],
)
def test_field_added_to_existing_doctype_with_constraint_requires_maintenance(attributes: dict) -> None:
    added = {"fieldname": "code", "fieldtype": "Data", **attributes}
    new = json.dumps({**TASK, "fields": [*TASK["fields"], added]})

    assert get_schema_migration_kind({DOCTYPE_PATH: "M"}, lambda path: json.dumps(TASK), lambda path: new) == "maintenance"
    assert get_schema_migration_kind({DOCTYPE_PATH: "A"}, lambda path: None, lambda path: new) == "online"


@pytest.mark.parametrize(
    ("status", "old", "new", "expected"),
    [
        ("A", None, json.dumps(TASK), "online"),
        ("A", None, "{", "maintenance"),
        ("A", None, '{"fields": [], "length": NaN}', "maintenance"),
        ("M", json.dumps(TASK), "[]", "maintenance"),
        ("M", json.dumps(TASK), None, "maintenance"),
        ("M", None, json.dumps(TASK), "maintenance"),
        ("M", "{", json.dumps(TASK), "maintenance"),
        ("D", json.dumps(TASK), None, "maintenance"),
        ("T", json.dumps(TASK), json.dumps(TASK), "maintenance"),
    ],
)
def test_doctype_path_changes(status: str, old: str | None, new: str | None, expected: str) -> None:
    assert get_schema_migration_kind({DOCTYPE_PATH: status}, lambda path: old, lambda path: new) == expected


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        (None, "maintenance"),
        ({}, "none"),
        ({"demo/demo/doctype/task/task.py": "M"}, "none"),
        ({"demo/patches.txt": "M"}, "maintenance"),
        ({"demo/modules.txt": "M"}, "maintenance"),
        ({"demo/hooks.py": "M"}, "maintenance"),
        ({"demo/fixtures/doctype/task/task.json": "A"}, "maintenance"),
        ({"demo/demo/custom/task.json": "A"}, "maintenance"),
        ({"frappe/geo/languages.json": "M"}, "maintenance"),
        ({DOCTYPE_PATH: "A", "demo/hooks.py": "M"}, "maintenance"),
        ({DOCTYPE_PATH: "A", "demo/public/task.js": "M"}, "online"),
    ],
)
def test_migration_kind_combines_changes(changes: dict[str, str] | None, expected: str) -> None:
    assert get_schema_migration_kind(changes, {}.get, {DOCTYPE_PATH: json.dumps(TASK)}.get) == expected
