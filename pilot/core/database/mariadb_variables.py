from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MariaDBValue = bool | int | str
VariableType = Literal["boolean", "integer", "string"]
VariableEditAction = Literal[
    "configuration",
    "innodb_buffer_pool_size",
    "max_connections",
    "performance_schema",
]

_BUFFER_POOL_COMPANION = (
    "Managed automatically with InnoDB Buffer Pool size so Pilot can enforce the host memory budget."
)
_MEMORY_SENSITIVE = "Read-only because an unsafe value can exhaust memory shared by MariaDB and the bench."
_STARTUP_SENSITIVE = "Read-only because Pilot owns this server-level startup setting."
_WORKLOAD_SENSITIVE = (
    "Read-only because an unsafe global value can interrupt Frappe requests, jobs, or migrations."
)
_REPLICATION_SENSITIVE = (
    "Read-only because changing binary logging or replication settings can affect recovery data."
)
_RECOVERY_SENSITIVE = (
    "Read-only because this is a recovery control that should only be used during incident response."
)
_SECURITY_SENSITIVE = (
    "Read-only because Pilot keeps this security-sensitive setting at a safe server default."
)
_PERFORMANCE_SCHEMA = (
    "Use the Performance Schema Quick action so Pilot can restart and verify MariaDB safely."
)


@dataclass(frozen=True)
class MariaDBVariableSpec:
    name: str
    label: str
    section: str
    description: str
    value_type: VariableType = "string"
    dynamic: bool = False
    edit_action: VariableEditAction | None = None
    minimum: int | None = None
    maximum: int | None = None
    step: int | None = None
    unit: str = ""
    read_only_reason: str = _STARTUP_SENSITIVE

    @property
    def option_name(self) -> str:
        return self.name.replace("_", "-")

    @property
    def editable(self) -> bool:
        return self.edit_action is not None

    def parse_server_value(self, raw_value: object) -> MariaDBValue:
        if self.value_type == "integer":
            try:
                return int(str(raw_value))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"MariaDB returned an invalid value for '{self.name}'.") from exc
        if self.value_type == "boolean":
            normalized = str(raw_value).strip().upper()
            if normalized in {"1", "ON", "TRUE"}:
                return True
            if normalized in {"0", "OFF", "FALSE"}:
                return False
            raise ValueError(f"MariaDB returned an invalid value for '{self.name}'.")
        return str(raw_value)

    def validate_input(self, value: object) -> MariaDBValue:
        self._require_generic_edit_action()
        if self.value_type == "integer":
            if type(value) is not int:
                raise ValueError(f"{self.label} must be a whole number.")
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"{self.label} must be at least {self.minimum}{self._unit_suffix()}.")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"{self.label} cannot be greater than {self.maximum}{self._unit_suffix()}.")
            return value
        if self.value_type == "boolean":
            if type(value) is not bool:
                raise ValueError(f"{self.label} must be either enabled or disabled.")
            return value
        raise ValueError(f"MariaDB variable '{self.name}' is not editable in Pilot.")

    def _require_generic_edit_action(self) -> None:
        if self.edit_action is None:
            raise ValueError(f"MariaDB variable '{self.name}' is read-only in Pilot.")
        if self.edit_action != "configuration":
            raise ValueError(
                f"MariaDB variable '{self.name}' must be changed through its guarded database action."
            )

    def option_value(self, value: object) -> str:
        validated = self.validate_input(value)
        if type(validated) is bool:
            return "ON" if validated else "OFF"
        return str(validated)

    def _unit_suffix(self) -> str:
        return f" {self.unit}" if self.unit else ""


def _editable_integer(
    name: str,
    label: str,
    section: str,
    description: str,
    minimum: int,
    maximum: int,
    *,
    unit: str = "",
) -> MariaDBVariableSpec:
    return MariaDBVariableSpec(
        name=name,
        label=label,
        section=section,
        description=description,
        value_type="integer",
        dynamic=True,
        edit_action="configuration",
        minimum=minimum,
        maximum=maximum,
        step=1,
        unit=unit,
        read_only_reason="",
    )


def _read_only(
    name: str,
    label: str,
    section: str,
    description: str,
    reason: str,
    *,
    value_type: VariableType = "string",
    dynamic: bool = False,
    unit: str = "",
) -> MariaDBVariableSpec:
    return MariaDBVariableSpec(
        name=name,
        label=label,
        section=section,
        description=description,
        value_type=value_type,
        dynamic=dynamic,
        unit=unit,
        read_only_reason=reason,
    )


MARIADB_VARIABLE_SPECS = (
    _editable_integer(
        "connect_timeout",
        "Connection handshake timeout",
        "Connections",
        "Seconds MariaDB waits for a client to finish its initial handshake.",
        2,
        60,
        unit="seconds",
    ),
    _editable_integer(
        "wait_timeout",
        "Idle connection timeout",
        "Connections",
        "Seconds an inactive non-interactive connection may remain open.",
        60,
        86400,
        unit="seconds",
    ),
    _editable_integer(
        "net_read_timeout",
        "Network read timeout",
        "Connections",
        "Seconds MariaDB waits for more data from a connected client.",
        5,
        600,
        unit="seconds",
    ),
    _editable_integer(
        "net_write_timeout",
        "Network write timeout",
        "Connections",
        "Seconds MariaDB waits while sending a block to a connected client.",
        5,
        600,
        unit="seconds",
    ),
    MariaDBVariableSpec(
        name="max_connections",
        label="Maximum connections",
        section="Connections",
        description="Maximum number of simultaneous client connections.",
        value_type="integer",
        dynamic=True,
        edit_action="max_connections",
        read_only_reason="",
    ),
    _read_only(
        "extra_max_connections",
        "Emergency connections",
        "Connections",
        "Connections reserved for administrative access when the main limit is full.",
        _MEMORY_SENSITIVE,
        value_type="integer",
        dynamic=True,
    ),
    _read_only(
        "max_user_connections",
        "Connections per user",
        "Connections",
        "Global default ceiling for simultaneous connections from one database user.",
        _WORKLOAD_SENSITIVE,
        value_type="integer",
        dynamic=True,
    ),
    _read_only(
        "net_buffer_length",
        "Initial network buffer",
        "Connections",
        "Starting per-connection network buffer size.",
        _MEMORY_SENSITIVE,
        value_type="integer",
        dynamic=True,
        unit="bytes",
    ),
    _read_only(
        "max_allowed_packet",
        "Maximum packet size",
        "Connections",
        "Largest packet accepted by MariaDB clients and the server.",
        _WORKLOAD_SENSITIVE,
        value_type="integer",
        dynamic=True,
        unit="bytes",
    ),
    _read_only(
        "extra_port",
        "Emergency connection port",
        "Connections",
        "Separate port used for administrative connections.",
        _STARTUP_SENSITIVE,
        value_type="integer",
    ),
    _editable_integer(
        "innodb_lock_wait_timeout",
        "InnoDB lock wait timeout",
        "InnoDB",
        "Seconds a statement waits for an InnoDB row lock before failing.",
        1,
        300,
        unit="seconds",
    ),
    _editable_integer(
        "innodb_old_blocks_pct",
        "InnoDB old buffer percentage",
        "InnoDB",
        "Percentage of the Buffer Pool LRU reserved for older pages.",
        5,
        95,
        unit="percent",
    ),
    _editable_integer(
        "innodb_old_blocks_time",
        "InnoDB old block delay",
        "InnoDB",
        "Delay before an old Buffer Pool page can move to the new sublist.",
        0,
        60000,
        unit="milliseconds",
    ),
    _editable_integer(
        "innodb_stats_persistent_sample_pages",
        "Persistent statistics sample pages",
        "InnoDB",
        "Index pages sampled when MariaDB calculates persistent optimizer statistics.",
        1,
        1024,
        unit="pages",
    ),
    MariaDBVariableSpec(
        name="innodb_print_all_deadlocks",
        label="Log all InnoDB deadlocks",
        section="InnoDB",
        description="Write every InnoDB deadlock report to the MariaDB error log.",
        value_type="boolean",
        dynamic=True,
        edit_action="configuration",
        read_only_reason="",
    ),
    _read_only(
        "innodb_status_output_locks",
        "InnoDB lock status output",
        "InnoDB",
        "Include extended lock details in periodic InnoDB status output.",
        "Read-only because enabling verbose lock output can produce substantial diagnostic logs.",
        value_type="boolean",
        dynamic=True,
    ),
    _read_only(
        "innodb_strict_mode",
        "InnoDB strict mode",
        "InnoDB",
        "Reject invalid or incompatible InnoDB table options instead of warning.",
        _SECURITY_SENSITIVE,
        value_type="boolean",
        dynamic=True,
    ),
    MariaDBVariableSpec(
        name="innodb_buffer_pool_size",
        label="InnoDB Buffer Pool size",
        section="InnoDB",
        description="Memory reserved for caching InnoDB data and indexes.",
        value_type="integer",
        dynamic=True,
        edit_action="innodb_buffer_pool_size",
        unit="bytes",
        read_only_reason="",
    ),
    _read_only(
        "innodb_buffer_pool_size_max",
        "InnoDB Buffer Pool live ceiling",
        "InnoDB",
        "MariaDB 11.8 ceiling for increasing the Buffer Pool without a restart.",
        _BUFFER_POOL_COMPANION,
        value_type="integer",
        unit="bytes",
    ),
    _read_only(
        "innodb_buffer_pool_size_auto_min",
        "InnoDB Buffer Pool automatic minimum",
        "InnoDB",
        "MariaDB 11.8 lower boundary for automatic Buffer Pool resizing.",
        _BUFFER_POOL_COMPANION,
        value_type="integer",
        unit="bytes",
    ),
    _read_only(
        "innodb_log_file_size",
        "InnoDB redo log size",
        "InnoDB",
        "Size of the InnoDB redo log used for crash recovery.",
        _MEMORY_SENSITIVE,
        value_type="integer",
        dynamic=True,
        unit="bytes",
    ),
    _read_only(
        "innodb_force_recovery",
        "InnoDB force recovery mode",
        "InnoDB",
        "Emergency recovery level used when normal InnoDB startup cannot complete.",
        _RECOVERY_SENSITIVE,
        value_type="integer",
    ),
    MariaDBVariableSpec(
        name="innodb_snapshot_isolation",
        label="InnoDB snapshot isolation",
        section="InnoDB",
        description="Controls write conflict detection for repeatable-read transactions.",
        value_type="boolean",
        dynamic=True,
        edit_action="configuration",
        read_only_reason="",
    ),
    _read_only(
        "innodb_flush_log_at_trx_commit",
        "Flush log at transaction commit",
        "InnoDB",
        "Controls transaction durability when MariaDB or the host crashes.",
        _SECURITY_SENSITIVE,
        value_type="integer",
        dynamic=True,
    ),
    _read_only(
        "tmp_table_size",
        "In-memory temporary table size",
        "Memory and temporary tables",
        "Per-connection ceiling before internal temporary tables move to disk.",
        _MEMORY_SENSITIVE,
        value_type="integer",
        dynamic=True,
        unit="bytes",
    ),
    _read_only(
        "max_heap_table_size",
        "MEMORY table size",
        "Memory and temporary tables",
        "Maximum size of user-created MEMORY tables and one temporary-table limit.",
        _MEMORY_SENSITIVE,
        value_type="integer",
        dynamic=True,
        unit="bytes",
    ),
    _read_only(
        "tmp_disk_table_size",
        "Temporary disk table size",
        "Memory and temporary tables",
        "Size limit used for internal temporary tables stored on disk.",
        _MEMORY_SENSITIVE,
        value_type="integer",
        dynamic=True,
        unit="bytes",
    ),
    _read_only(
        "key_buffer_size",
        "MyISAM key buffer size",
        "Memory and temporary tables",
        "Memory reserved for MyISAM index blocks.",
        _MEMORY_SENSITIVE,
        value_type="integer",
        dynamic=True,
        unit="bytes",
    ),
    _read_only(
        "tmpdir",
        "Temporary file directory",
        "Memory and temporary tables",
        "Directory MariaDB uses for server-side temporary files.",
        _STARTUP_SENSITIVE,
    ),
    _read_only(
        "max_statement_time",
        "Maximum statement time",
        "Query behavior",
        "Global time limit after which MariaDB aborts a statement.",
        _WORKLOAD_SENSITIVE,
        dynamic=True,
    ),
    _read_only(
        "long_query_time",
        "Slow query threshold",
        "Query behavior",
        "Execution time after which a query is counted as slow.",
        "Read-only because MariaDB 11.8 uses version-specific slow-query controls.",
        dynamic=True,
        unit="seconds",
    ),
    _read_only(
        "binlog_expire_logs_seconds",
        "Binary log retention",
        "Binary logging and replication",
        "Seconds before eligible binary logs expire.",
        "Use Manage Binlogs so Pilot can inspect and purge complete log ranges safely.",
        value_type="integer",
        dynamic=True,
        unit="seconds",
    ),
    _read_only(
        "expire_logs_days",
        "Legacy binary log retention",
        "Binary logging and replication",
        "Legacy day-based retention period for binary logs.",
        _REPLICATION_SENSITIVE,
        dynamic=True,
        unit="days",
    ),
    _read_only(
        "log_bin",
        "Binary logging",
        "Binary logging and replication",
        "Whether MariaDB writes binary logs for recovery and replication.",
        _REPLICATION_SENSITIVE,
    ),
    _read_only(
        "binlog_format",
        "Binary log format",
        "Binary logging and replication",
        "How data changes are represented in the binary log.",
        _REPLICATION_SENSITIVE,
    ),
    _read_only(
        "log_slave_updates",
        "Log replica updates",
        "Binary logging and replication",
        "Whether updates received from a primary are written to this server's binary log.",
        _REPLICATION_SENSITIVE,
    ),
    _read_only(
        "slave_connections_needed_for_purge",
        "Replica connections needed for purge",
        "Binary logging and replication",
        "MariaDB 11.8 replica-connection threshold used before old row versions are purged.",
        _REPLICATION_SENSITIVE,
        value_type="integer",
    ),
    _read_only(
        "read_only",
        "Read-only mode",
        "Security and recovery",
        "Prevents ordinary database users from changing non-temporary tables.",
        _RECOVERY_SENSITIVE,
        dynamic=True,
    ),
    _read_only(
        "local_infile",
        "Local file imports",
        "Security and recovery",
        "Allows clients to upload local files through LOAD DATA LOCAL INFILE.",
        _SECURITY_SENSITIVE,
        value_type="boolean",
        dynamic=True,
    ),
    _read_only(
        "myisam_recover_options",
        "MyISAM recovery options",
        "Security and recovery",
        "Recovery behavior used when MariaDB opens a damaged MyISAM table.",
        _RECOVERY_SENSITIVE,
    ),
    MariaDBVariableSpec(
        name="performance_schema",
        label="Performance Schema",
        section="Performance Schema",
        description="Collects instrumentation used by database performance diagnostics.",
        value_type="boolean",
        edit_action="performance_schema",
        read_only_reason="",
    ),
    _read_only(
        "performance_schema_instrument",
        "Performance Schema instruments",
        "Performance Schema",
        "Startup rules controlling which operations Performance Schema instruments.",
        _PERFORMANCE_SCHEMA,
    ),
    _read_only(
        "performance_schema_consumer_events_statements_current",
        "Current statement events",
        "Performance Schema",
        "Collect current statement instrumentation events.",
        _PERFORMANCE_SCHEMA,
        value_type="boolean",
    ),
    _read_only(
        "performance_schema_consumer_events_statements_history",
        "Statement event history",
        "Performance Schema",
        "Collect short statement instrumentation history.",
        _PERFORMANCE_SCHEMA,
        value_type="boolean",
    ),
    _read_only(
        "performance_schema_consumer_events_statements_history_long",
        "Long statement event history",
        "Performance Schema",
        "Collect long statement instrumentation history.",
        _PERFORMANCE_SCHEMA,
        value_type="boolean",
    ),
    _read_only(
        "performance_schema_consumer_events_stages_current",
        "Current stage events",
        "Performance Schema",
        "Collect current execution-stage instrumentation events.",
        _PERFORMANCE_SCHEMA,
        value_type="boolean",
    ),
    _read_only(
        "performance_schema_consumer_events_stages_history",
        "Stage event history",
        "Performance Schema",
        "Collect short execution-stage instrumentation history.",
        _PERFORMANCE_SCHEMA,
        value_type="boolean",
    ),
    _read_only(
        "performance_schema_consumer_events_stages_history_long",
        "Long stage event history",
        "Performance Schema",
        "Collect long execution-stage instrumentation history.",
        _PERFORMANCE_SCHEMA,
        value_type="boolean",
    ),
    _read_only(
        "performance_schema_consumer_events_waits_current",
        "Current wait events",
        "Performance Schema",
        "Collect current wait instrumentation events.",
        _PERFORMANCE_SCHEMA,
        value_type="boolean",
    ),
    _read_only(
        "performance_schema_consumer_events_waits_history",
        "Wait event history",
        "Performance Schema",
        "Collect short wait instrumentation history.",
        _PERFORMANCE_SCHEMA,
        value_type="boolean",
    ),
    _read_only(
        "performance_schema_consumer_events_waits_history_long",
        "Long wait event history",
        "Performance Schema",
        "Collect long wait instrumentation history.",
        _PERFORMANCE_SCHEMA,
        value_type="boolean",
    ),
)

MARIADB_VARIABLES_BY_NAME = {spec.name: spec for spec in MARIADB_VARIABLE_SPECS}
MARIADB_VARIABLE_NAMES = frozenset(MARIADB_VARIABLES_BY_NAME)
EDITABLE_MARIADB_VARIABLE_NAMES = frozenset(spec.name for spec in MARIADB_VARIABLE_SPECS if spec.editable)
GENERIC_EDITABLE_MARIADB_VARIABLE_NAMES = frozenset(
    spec.name for spec in MARIADB_VARIABLE_SPECS if spec.edit_action == "configuration"
)


def mariadb_variable_spec(name: str) -> MariaDBVariableSpec:
    try:
        return MARIADB_VARIABLES_BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"MariaDB variable '{name}' is not supported by Pilot.") from exc
