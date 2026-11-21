from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Literal

MigrationKind = Literal["none", "online", "maintenance"]

DOCTYPE_PATH = re.compile(r"\w+/\w+/\w+/(.+)/\1\.json")
MAINTENANCE_PATHS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\w+/patches\.txt",
        r"\w+/modules\.txt",
        r"\w+/hooks\.py",
        r"\w+/fixtures/",
        r"\w+/\w+/custom/",
        r"\w+/(?:sidebar|dock)/[^/]+/[^/]+\.json$",
        r"\w+/\w+/doctype/[^/]+/(?:list_filter|kanban_board)/[^/]+\.json$",
        r"\w+/\w+/doctype_settings_map/[^/]+\.json$",
        r"frappe/geo/languages\.json",
    )
)
DOCTYPE_UI_KEYS = {"modified", "field_order"}
FIELD_UI_KEYS = {
    "bold",
    "collapsible",
    "columns",
    "depends_on",
    "description",
    "hidden",
    "idx",
    "in_list_view",
    "in_standard_filter",
    "insert_after",
    "label",
    "placeholder",
    "print_hide",
    "read_only",
    "translatable",
    "width",
}
# Expand can add a column before apply publishes a required-field rule.
ADDED_FIELD_MAINTENANCE_KEYS = ("reqd", "unique", "search_index", "not_nullable", "default")
FIELDS_SKIPPED_BY_EXPAND = {"Link", "Check", "Int", "Currency", "Float", "Percent"}


def get_schema_migration_kind(
    changes: dict[str, str] | None,
    read_old: Callable[[str], str | None],
    read_new: Callable[[str], str | None],
) -> MigrationKind:
    """Classify whether an update needs model sync and whether it can expand."""
    if changes is None:
        return "maintenance"
    kind: MigrationKind = "none"
    for path, status in changes.items():
        if any(pattern.match(path) for pattern in MAINTENANCE_PATHS):
            return "maintenance"
        if not DOCTYPE_PATH.match(path):
            continue
        if "/doctype/" not in path or not DOCTYPE_PATH.fullmatch(path) or not _is_additive(status, path, read_old, read_new):
            return "maintenance"
        kind = "online"
    return kind


def _is_additive(
    status: str,
    path: str,
    read_old: Callable[[str], str | None],
    read_new: Callable[[str], str | None],
) -> bool:
    if status not in {"A", "M"}:
        return False
    new = _get_doctype(read_new(path))
    if new is None:
        return False
    if status == "A":
        return all(field.get("fieldtype") != "Link" for field in new["fields"].values())
    old = _get_doctype(read_old(path))
    if old is None:
        return False
    if not _has_same_metadata(old, new, DOCTYPE_UI_KEYS | {"fields"}):
        return False
    old_fields = old["fields"]
    new_fields = new["fields"]
    if not all(_can_expand_field(new_fields[name]) for name in new_fields.keys() - old_fields.keys()):
        return False
    return all(
        name in new_fields and _has_same_metadata(field, new_fields[name], FIELD_UI_KEYS)
        for name, field in old_fields.items()
    )


def _can_expand_field(field: dict) -> bool:
    return (
        field.get("fieldtype") not in FIELDS_SKIPPED_BY_EXPAND
        and not any(field.get(key) for key in ADDED_FIELD_MAINTENANCE_KEYS)
    )


def _has_same_metadata(old: dict, new: dict, ignored_keys: set[str]) -> bool:
    old_metadata = {key: old[key] for key in old.keys() - ignored_keys}
    new_metadata = {key: new[key] for key in new.keys() - ignored_keys}
    try:
        return json.dumps(old_metadata, sort_keys=True) == json.dumps(new_metadata, sort_keys=True)
    except RecursionError:
        return False


def _get_doctype(contents: str | None) -> dict | None:
    if contents is None:
        return None
    try:
        doctype = json.loads(contents, parse_constant=_reject_json_constant)
    except (ValueError, RecursionError):
        return None
    if not isinstance(doctype, dict) or doctype.get("doctype", "DocType") != "DocType":
        return None
    fields = _get_fields(doctype.get("fields"))
    if fields is None:
        return None
    return {**doctype, "fields": fields}


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"{value} is not a JSON number")


def _get_fields(fields: object) -> dict[str, dict] | None:
    if not isinstance(fields, list):
        return None
    by_name = {}
    for field in fields:
        name = field.get("fieldname") if isinstance(field, dict) else None
        if not isinstance(name, str) or not name or name in by_name:
            return None
        by_name[name] = field
    return by_name
