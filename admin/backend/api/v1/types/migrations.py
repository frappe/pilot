from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from admin.backend.api.responses import PageMeta
from pilot.managers.task import TaskStatus

MigrationStateName = Literal[
    "preparing",
    "backing_up",
    "updating",
    "migrating",
    "needs_attention",
    "retrying",
    "reverting_apps",
    "reverting_sites",
    "restarting",
    "revert_failed",
    "completed",
    "reverted",
]


class MigrationApp(TypedDict):
    name: str
    sha: str
    repository_url: str
    updated_sha: str | None
    target_sha: str | None
    target_kind: Literal["tag", "commit"]
    compare_url: str | None


class MigrationSite(TypedDict):
    name: str
    original_config: dict
    backup_status: Literal["pending", "backing_up", "backed_up", "failed"]
    touched_tables: list[str]
    touched_tables_trusted: bool
    migration_status: Literal["pending", "running", "success", "failed", "recovering", "recovered"]


class MigrationDiagnosis(TypedDict):
    message: str
    output_excerpt: str
    phase: NotRequired[Literal["backing_up", "update", "migrate"]]
    patch: NotRequired[str | None]
    table: NotRequired[str | None]
    column: NotRequired[str | None]
    database_engine: NotRequired[str]
    failure_kind: NotRequired[
        Literal[
            "string_to_number",
            "data_truncated",
            "unknown_column",
            "duplicate_entry",
            "missing_table",
            "unknown",
        ]
    ]
    resolver_id: NotRequired[str | None]


class MigrationDecision(TypedDict):
    action: Literal["bypass_patch"]
    site: str | None
    patch: str
    at: str


class MigrationTaskLog(TypedDict):
    id: str
    label: str
    site: str | None
    status: TaskStatus | None


class MigrationPendingAction(TypedDict):
    role: str
    task_id: str
    status: TaskStatus


class MigrationSummary(TypedDict):
    id: str
    kind: Literal["update", "site_migrate"]
    state: MigrationStateName
    created_at: str
    started_at: str | None
    finished_at: str | None
    apps: list[MigrationApp]
    apps_filter: list[str] | None
    apps_updated: bool
    sites: list[MigrationSite]
    failed_site: str | None
    diagnosis: MigrationDiagnosis | None
    safeguards_disabled: bool
    return_state: MigrationStateName | None
    root_task_id: str | None
    task_ids: dict[str, str]
    chain: list[dict]
    revert_checkpoints: dict[str, bool]
    decisions: list[MigrationDecision]
    can_restore: bool
    task_logs: list[MigrationTaskLog]
    pending_action: MigrationPendingAction | None


class MigrationAccepted(TypedDict):
    operation: MigrationSummary
    task_id: str


class MigrationPage(TypedDict):
    data: list[MigrationSummary]
    meta: PageMeta
