#!/usr/bin/env python3
"""Generate TypeScript interfaces from backend response types. Run with:
    uv run python scripts/generate_ts_types.py

A target root is a dataclass/TypedDict, or a module whose own dataclasses and
TypedDicts are all emitted. A nested type owned by another target is imported
from that file; any other nested type is inlined.
"""

from __future__ import annotations

import types
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Literal,
    NotRequired,
    Required,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

from admin.backend.api import responses
from admin.backend.api.v1 import core, logs, notifications, ssh_keys, updates
from admin.backend.api.v1.sites import central as site_central
from admin.backend.api.v1.sites import monitoring as site_monitoring
from admin.backend.api.v1.sites import uptime as site_uptime
from admin.backend.api.v1.types import (
    apps,
    auth,
    benches,
    database,
    git,
    migrations,
    settings,
    setup,
    site_apps,
    site_backups,
    site_domains,
    sites,
    stats,
    tasks,
)
from admin.backend.providers.apps import AppInfo
from admin.backend.providers.storage import StorageBreakdown
from pilot.core.bench.audit_log import AuditEntry
from pilot.core.database.base import (
    BinlogFile,
    DatabaseProcess,
    DatabaseSize,
    FullTableScanQuery,
    LockWaitRow,
    PerformanceSection,
    RedundantIndex,
    TableSize,
    TimeConsumingQuery,
    UnusedIndex,
)
from pilot.core.database.configurations import DatabaseConfigurationEditor
from pilot.core.notification import Notification
from pilot.core.site.storage import SiteStorageReport
from pilot.managers.task.reader import DoneEvent, OutputEvent, StatusEvent

_TARGETS = (
    ((StorageBreakdown,), "storage.ts"),
    ((SiteStorageReport,), "siteStorage.ts"),
    ((AuditEntry,), "audit.ts"),
    ((Notification, notifications), "notification.ts"),
    ((responses,), "common.ts"),
    ((tasks, OutputEvent, StatusEvent, DoneEvent), "tasks.ts"),
    ((sites, site_central), "sites.ts"),
    ((site_apps,), "siteApps.ts"),
    ((site_backups,), "siteBackups.ts"),
    ((site_domains,), "siteDomains.ts"),
    ((site_monitoring, site_uptime), "siteMonitoring.ts"),
    ((AppInfo, apps), "apps.ts"),
    ((auth,), "auth.ts"),
    ((benches,), "benches.ts"),
    ((core,), "core.ts"),
    ((git,), "git.ts"),
    ((logs,), "logs.ts"),
    ((migrations,), "migrations.ts"),
    ((settings,), "settings.ts"),
    ((setup,), "setup.ts"),
    ((ssh_keys,), "sshKeys.ts"),
    ((stats,), "stats.ts"),
    ((updates,), "updates.ts"),
    (
        (
            database,
            DatabaseProcess,
            LockWaitRow,
            PerformanceSection,
            TimeConsumingQuery,
            FullTableScanQuery,
            UnusedIndex,
            RedundantIndex,
            DatabaseSize,
            TableSize,
            BinlogFile,
            DatabaseConfigurationEditor,
        ),
        "database.ts",
    ),
)
_OUTPUT_DIR = "admin/frontend/dashboard/src/types"
_PRIMITIVES = {str: "string", int: "number", float: "number", bool: "boolean", type(None): "null"}
_UNKNOWN = {Any, object, dict, list}


def _is_union(tp) -> bool:
    return isinstance(tp, types.UnionType) or get_origin(tp) is Union


def _is_schema(tp) -> bool:
    return isinstance(tp, type) and (is_dataclass(tp) or is_typeddict(tp))


def _literal(value) -> str:
    return f"'{value}'" if isinstance(value, str) else str(value).lower()


def _ts_type(tp) -> str:
    origin, args = get_origin(tp), get_args(tp)
    if _is_union(tp):
        return " | ".join(dict.fromkeys(_ts_type(arg) for arg in args))
    if origin is Literal:
        return " | ".join(_literal(arg) for arg in args)
    if origin in (list, tuple, set, frozenset):
        item = _ts_type(args[0]) if args else "unknown"
        return f"({item})[]" if " | " in item else f"{item}[]"
    if origin is dict:
        return f"Record<string, {_ts_type(args[1])}>"
    return _named_type(tp)


def _named_type(tp) -> str:
    if isinstance(tp, type) and issubclass(tp, Enum):
        return " | ".join(_literal(member.value) for member in tp)
    if _is_schema(tp):
        return tp.__name__
    if tp in _PRIMITIVES:
        return _PRIMITIVES[tp]
    if tp in _UNKNOWN:
        return "Record<string, unknown>" if tp is dict else "unknown[]" if tp is list else "unknown"
    raise TypeError(f"No TypeScript mapping for {tp!r}")


def _fields(schema) -> list[tuple[str, Any, bool]]:
    """(name, type, optional) per field, with Required/NotRequired unwrapped."""
    hints = get_type_hints(schema, include_extras=True)
    if is_dataclass(schema):
        return [(field.name, hints[field.name], False) for field in fields(schema)]
    rows = []
    for name, hint in hints.items():
        # __required_keys__ misses NotRequired written as a string annotation.
        marker = get_origin(hint)
        if marker in (Required, NotRequired):
            (hint,) = get_args(hint)
        optional = marker is NotRequired or (marker is not Required and name not in schema.__required_keys__)
        rows.append((name, hint, optional))
    return rows


def _nested_schemas(tp):
    if _is_schema(tp):
        yield tp
        return
    for arg in get_args(tp):
        yield from _nested_schemas(arg)


def _module_schemas(module) -> list:
    return [
        value for value in vars(module).values() if _is_schema(value) and value.__module__ == module.__name__
    ]


class TypeFile:
    def __init__(self, roots: tuple, file_name: str) -> None:
        self.file_name = file_name
        self.ts_name = file_name.removesuffix(".ts")
        self.exports = []
        for root in roots:
            self.exports.extend(_module_schemas(root) if isinstance(root, types.ModuleType) else [root])

    def render(self, owners: dict[type, TypeFile]) -> str:
        seen: set = set()
        order: list = []
        imports: dict[str, set[str]] = {}
        for schema in self.exports:
            self._collect(schema, owners, seen, order, imports)
        header = ["// AUTO-GENERATED by scripts/generate_ts_types.py - do not edit by hand.", ""]
        for ts_name, names in sorted(imports.items()):
            header.append(f"import type {{ {', '.join(sorted(names))} }} from '@/types/{ts_name}'")
        if imports:
            header.append("")
        return "\n".join(header) + "\n" + "\n\n".join(_render(schema) for schema in order) + "\n"

    def _collect(self, schema, owners, seen, order, imports) -> None:
        owner = owners.get(schema, self)
        if owner is not self:
            imports.setdefault(owner.ts_name, set()).add(schema.__name__)
            return
        if schema in seen:
            return
        seen.add(schema)
        for _, hint, _ in _fields(schema):
            for nested in _nested_schemas(hint):
                self._collect(nested, owners, seen, order, imports)
        order.append(schema)


def _render(schema) -> str:
    lines = [f"export interface {schema.__name__} {{"]
    for name, hint, optional in _fields(schema):
        lines.append(f"  {name}{'?' if optional else ''}: {_ts_type(hint)}")
    lines.append("}")
    return "\n".join(lines)


def render_all(targets) -> dict[str, str]:
    """TypeScript source keyed by file name."""
    files = [TypeFile(roots, file_name) for roots, file_name in targets]
    owners: dict[type, TypeFile] = {}
    for type_file in files:
        for schema in type_file.exports:
            if schema in owners:
                raise ValueError(f"{schema.__name__} is a root of more than one target")
            owners[schema] = type_file
    return {type_file.file_name: type_file.render(owners) for type_file in files}


def main() -> None:
    output_dir = Path(__file__).resolve().parent.parent / _OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    for file_name, source in render_all(_TARGETS).items():
        path = output_dir / file_name
        path.write_text(source)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
