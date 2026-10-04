from __future__ import annotations

from typing import Literal, TypedDict

from pilot.core.database.base import BinlogStatus, LockWaitStatus
from pilot.core.database.configurations import DatabaseConfigurationEditor
from pilot.core.database.mariadb_variables import MariaDBValue, VariableType


class DatabaseSite(TypedDict):
    name: str
    db_type: str
    db_name: str


class TableColumn(TypedDict):
    name: str
    type: str


class TableSchema(TypedDict):
    name: str
    columns: list[TableColumn]


class ExecutedQuery(TypedDict):
    columns: list[str]
    rows: list[list[object]]
    row_count: int
    duration_ms: float
    truncated: bool
    affected_rows: int


class UnsupportedDatabaseDiagnostics(TypedDict):
    engine: str
    supported: Literal[False]
    reason: str


class DatabaseDiagnostics(TypedDict):
    engine: str
    supported: Literal[True]
    active_connections: int
    lock_waits: LockWaitStatus
    binlog: BinlogStatus | None
    performance_schema_enabled: bool


class DatabaseActionCapability(TypedDict):
    available: bool
    reason: str


class RestartCapability(DatabaseActionCapability):
    requires_restart: bool


class PerformanceSchemaCapability(DatabaseActionCapability):
    enabled: bool | None
    requires_restart: bool


class InnoDBBufferPoolCapability(DatabaseActionCapability):
    current_mb: int | None
    min_mb: int | None
    max_mb: int | None
    recommended_mb: int | None
    dynamic_max_mb: int | None
    unit: Literal["MB"]
    requires_restart: bool


class MaxConnectionsCapability(DatabaseActionCapability):
    current: int | None
    min: int | None
    max: int | None
    recommended: int | None
    requires_restart: bool


class DatabaseActionCapabilities(TypedDict):
    restart: RestartCapability
    performance_schema: PerformanceSchemaCapability
    innodb_buffer_pool_size: InnoDBBufferPoolCapability
    max_connections: MaxConnectionsCapability
    manage_binlogs: DatabaseActionCapability


class DatabaseCapabilities(TypedDict):
    engine: str
    managed: bool
    reachable: bool
    actions: DatabaseActionCapabilities


class DatabaseVariable(TypedDict):
    name: str
    label: str
    section: str
    description: str
    value: MariaDBValue | None
    value_type: VariableType
    unit: str
    dynamic: bool
    requires_restart: bool
    supported: bool
    editable: bool
    edit: DatabaseConfigurationEditor | None
    reason: str
    min: int | None
    max: int | None
    step: int | None


class DatabaseConfigurationSnapshot(TypedDict):
    engine: str
    managed: bool
    readable: bool
    editable: bool
    reason: str
    edit_reason: str
    variables: list[DatabaseVariable]


class ActionStatus(TypedDict):
    status: Literal["ok"]
