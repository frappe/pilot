from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, TypedDict

from pilot.core.database.mariadb_variables import (
    MARIADB_VARIABLE_SPECS,
    MariaDBValue,
    MariaDBVariableSpec,
    VariableEditAction,
    VariableType,
    mariadb_variable_spec,
)
from pilot.core.database.quick_actions import DatabaseQuickActions
from pilot.exceptions import DatabaseError
from pilot.managers.database.mariadb import MariaDBManager
from pilot.managers.database.memory import MEMORY_LIMIT_NAMES
from pilot.managers.platform import is_linux

if TYPE_CHECKING:
    from pilot.config import BenchConfig


_MARIADB_ONLY = "Database configurations are available only when the bench uses MariaDB."
_UNREACHABLE = "MariaDB is not reachable with Pilot's admin credentials."
_EXTERNAL_READ_ONLY = (
    "External MariaDB configurations are read-only. Pilot changes only the MariaDB instance it "
    "provisioned on this server."
)
_MEBIBYTE = 1024 * 1024


class DatabaseConfigurationEditor(TypedDict):
    action: VariableEditAction
    value: MariaDBValue | None
    value_type: VariableType
    unit: str
    min: int | None
    max: int | None
    step: int | None
    recommended: int | None
    dynamic_max: int | None
    requires_restart: bool


class DatabaseConfigurationChange(TypedDict):
    action: VariableEditAction
    name: str
    value: MariaDBValue
    current: MariaDBValue
    changed: bool


class GuardedEditor(TypedDict):
    edit: DatabaseConfigurationEditor | None
    reason: str


class DatabaseConfigurations:
    """Read the MariaDB catalog and authorize its small editable subset."""

    def __init__(
        self,
        config: BenchConfig,
        manager: MariaDBManager | None = None,
        quick_actions: DatabaseQuickActions | None = None,
    ) -> None:
        self.config = config
        self.manager = (
            manager
            if manager is not None
            else (MariaDBManager(config.mariadb) if config.db_type == "mariadb" else None)
        )
        self.quick_actions = quick_actions

    def snapshot(self) -> dict:
        read_reason = self._read_reason()
        managed = self.config.db_type == "mariadb" and not self.config.mariadb.existing
        if read_reason:
            return {
                "engine": self.config.db_type,
                "managed": managed,
                "readable": False,
                "editable": False,
                "reason": read_reason,
                "edit_reason": read_reason,
                "variables": [],
            }

        manager = self._manager()
        raw_values = manager.global_variable_values(spec.name for spec in MARIADB_VARIABLE_SPECS)
        edit_reason = self._edit_reason()
        values = self._parse_values(raw_values)
        guarded_editors = self._guarded_editors(values) if not edit_reason else {}
        variables = []
        for spec in MARIADB_VARIABLE_SPECS:
            supported = spec.name in values
            value = values.get(spec.name)

            edit = self._editor(spec, value, guarded_editors) if supported and not edit_reason else None
            editable = edit is not None
            if not supported:
                variable_reason = "This variable is not exposed by the installed MariaDB version."
            elif not spec.editable:
                variable_reason = spec.read_only_reason
            elif edit_reason:
                variable_reason = edit_reason
            elif not editable:
                guarded_editor = (
                    guarded_editors.get(spec.edit_action) if spec.edit_action is not None else None
                )
                variable_reason = (
                    guarded_editor["reason"]
                    if guarded_editor is not None
                    else "This variable cannot be changed on the current MariaDB server."
                )
            else:
                variable_reason = ""

            variables.append(
                {
                    "name": spec.name,
                    "label": spec.label,
                    "section": spec.section,
                    "description": spec.description,
                    "value": value,
                    "value_type": spec.value_type,
                    "unit": spec.unit,
                    "dynamic": spec.dynamic,
                    "requires_restart": not spec.dynamic,
                    "supported": supported,
                    "editable": editable,
                    "edit": edit,
                    "reason": variable_reason,
                    "min": spec.minimum,
                    "max": spec.maximum,
                    "step": spec.step,
                }
            )
        if managed and is_linux():
            variables.extend(manager.memory.get_variables(edit_reason))
        return {
            "engine": self.config.db_type,
            "managed": managed,
            "readable": True,
            "editable": any(variable["editable"] for variable in variables),
            "reason": "",
            "edit_reason": edit_reason,
            "variables": variables,
        }

    def prepare_change(self, name: str, value: object) -> DatabaseConfigurationChange:
        if name in MEMORY_LIMIT_NAMES:
            reason = self._edit_reason(require_reachable=True)
            if reason:
                raise DatabaseError(reason)
            manager = self._manager()
            limits = manager.memory.validate(name, value)
            current = manager.memory.get_limits()[name]
            return {
                "action": "configuration",
                "name": name,
                "value": limits[name],
                "current": current,
                "changed": current != value,
            }
        spec = mariadb_variable_spec(name)
        if not spec.editable:
            spec.validate_input(value)
        edit_reason = self._edit_reason(require_reachable=True)
        if edit_reason:
            raise DatabaseError(edit_reason)

        if spec.edit_action != "configuration":
            return self._prepare_guarded_change(spec, value)

        requested = spec.validate_input(value)

        raw_value = self._manager().global_variable_values((name,)).get(name)
        if raw_value is None:
            raise DatabaseError(f"MariaDB variable '{name}' is not exposed by the installed MariaDB version.")
        try:
            current = spec.parse_server_value(raw_value)
        except ValueError as exc:
            raise DatabaseError(str(exc)) from exc
        return {
            "action": "configuration",
            "name": name,
            "value": requested,
            "current": current,
            "changed": current != requested,
        }

    def set(self, name: str, value: object) -> bool:
        if name in MEMORY_LIMIT_NAMES:
            reason = self._edit_reason(require_reachable=True)
            if reason:
                raise DatabaseError(reason)
            self._manager().memory.set(name, value)
            return True
        spec = mariadb_variable_spec(name)
        requested: MariaDBValue = spec.validate_input(value)
        edit_reason = self._edit_reason(require_reachable=True)
        if edit_reason:
            raise DatabaseError(edit_reason)
        return self._manager().set_configuration_variable(name, requested)

    def _editor(
        self,
        spec: MariaDBVariableSpec,
        value: MariaDBValue | None,
        guarded_editors: Mapping[VariableEditAction, GuardedEditor],
    ) -> DatabaseConfigurationEditor | None:
        action = spec.edit_action
        if action is None:
            return None
        if action == "configuration":
            return self._editor_contract(
                action=action,
                value=value,
                value_type=spec.value_type,
                unit=spec.unit,
                minimum=spec.minimum,
                maximum=spec.maximum,
                step=spec.step,
                requires_restart=not spec.dynamic,
            )

        guarded_editor = guarded_editors.get(action)
        return guarded_editor["edit"] if guarded_editor is not None else None

    def _parse_values(self, raw_values: Mapping[str, object]) -> dict[str, MariaDBValue]:
        values: dict[str, MariaDBValue] = {}
        for spec in MARIADB_VARIABLE_SPECS:
            if spec.name not in raw_values:
                continue
            try:
                values[spec.name] = spec.parse_server_value(raw_values[spec.name])
            except ValueError as exc:
                raise DatabaseError(str(exc)) from exc
        return values

    def _guarded_editors(
        self,
        values: Mapping[str, MariaDBValue],
    ) -> dict[VariableEditAction, GuardedEditor]:
        editors: dict[VariableEditAction, GuardedEditor] = {}
        if "performance_schema" in values:
            editors["performance_schema"] = {
                "edit": self._editor_contract(
                    action="performance_schema",
                    value=values["performance_schema"],
                    value_type="boolean",
                    requires_restart=True,
                ),
                "reason": "",
            }

        sizing_actions: tuple[VariableEditAction, ...] = (
            "innodb_buffer_pool_size",
            "max_connections",
        )
        if not any(action in values for action in sizing_actions):
            return editors
        try:
            limits = self._manager().variable_limits()
        except DatabaseError as exc:
            for action in sizing_actions:
                editors[action] = {"edit": None, "reason": str(exc)}
            return editors

        if "max_connections" in values:
            editors["max_connections"] = {
                "edit": self._editor_contract(
                    action="max_connections",
                    value=values["max_connections"],
                    value_type="integer",
                    minimum=limits.max_connections_min,
                    maximum=limits.max_connections_max,
                    step=1,
                    recommended=limits.max_connections_recommended,
                    requires_restart=False,
                ),
                "reason": "",
            }

        if "innodb_buffer_pool_size" in values:
            buffer_pool_size = values["innodb_buffer_pool_size"]
            dynamic_max = values.get("innodb_buffer_pool_size_max")
            if type(buffer_pool_size) is not int or type(dynamic_max) is not int:
                editors["innodb_buffer_pool_size"] = {
                    "edit": None,
                    "reason": "The installed MariaDB version does not expose the live Buffer Pool ceiling.",
                }
            else:
                editors["innodb_buffer_pool_size"] = {
                    "edit": self._editor_contract(
                        action="innodb_buffer_pool_size",
                        value=buffer_pool_size // _MEBIBYTE,
                        value_type="integer",
                        unit="MB",
                        minimum=limits.innodb_buffer_pool_min_mb,
                        maximum=limits.innodb_buffer_pool_max_mb,
                        step=1,
                        recommended=limits.innodb_buffer_pool_recommended_mb,
                        dynamic_max=dynamic_max // _MEBIBYTE,
                        requires_restart=False,
                    ),
                    "reason": "",
                }
        return editors

    @staticmethod
    def _editor_contract(
        *,
        action: VariableEditAction,
        value: MariaDBValue | None,
        value_type: VariableType,
        unit: str = "",
        minimum: int | None = None,
        maximum: int | None = None,
        step: int | None = None,
        recommended: int | None = None,
        dynamic_max: int | None = None,
        requires_restart: bool,
    ) -> DatabaseConfigurationEditor:
        return {
            "action": action,
            "value": value,
            "value_type": value_type,
            "unit": unit,
            "min": minimum,
            "max": maximum,
            "step": step,
            "recommended": recommended,
            "dynamic_max": dynamic_max,
            "requires_restart": requires_restart,
        }

    def _prepare_guarded_change(
        self,
        spec: MariaDBVariableSpec,
        value: object,
    ) -> DatabaseConfigurationChange:
        actions = self._quick_actions()
        if spec.edit_action == "performance_schema":
            if type(value) is not bool:
                raise ValueError("Performance Schema must be either enabled or disabled.")
            capability = actions.require_performance_schema()
            current = capability["enabled"]
        elif spec.edit_action == "innodb_buffer_pool_size":
            if type(value) is not int:
                raise ValueError("InnoDB Buffer Pool size must be a whole number.")
            capability = actions.require_innodb_buffer_pool_size(value)
            current = capability["current_mb"]
        elif spec.edit_action == "max_connections":
            if type(value) is not int:
                raise ValueError("Maximum connections must be a whole number.")
            capability = actions.require_max_connections(value)
            current = capability["current"]
        else:
            raise ValueError(f"MariaDB variable '{spec.name}' is read-only in Pilot.")
        return {
            "action": spec.edit_action,
            "name": spec.name,
            "value": value,
            "current": current,
            "changed": current != value,
        }

    def _read_reason(self) -> str:
        if self.config.db_type != "mariadb":
            return _MARIADB_ONLY
        manager = self._manager()
        if not self.config.mariadb.existing:
            if not manager.is_installed():
                return "MariaDB is not installed on this server."
            if not manager.is_provisioned():
                return "Pilot's MariaDB server has not been provisioned."
        if not manager.is_healthy():
            return _UNREACHABLE
        return ""

    def _edit_reason(self, *, require_reachable: bool = False) -> str:
        if self.config.db_type != "mariadb":
            return _MARIADB_ONLY
        if self.config.mariadb.existing:
            return _EXTERNAL_READ_ONLY
        if not self.config.admin.allow_bench_management:
            return "Bench management is disabled on this server."
        if not is_linux():
            return "MariaDB configuration changes are available only on Linux."
        if require_reachable:
            return self._read_reason()
        return ""

    def _manager(self) -> MariaDBManager:
        if self.manager is None:
            raise DatabaseError(_MARIADB_ONLY)
        return self.manager

    def _quick_actions(self) -> DatabaseQuickActions:
        if self.quick_actions is None:
            self.quick_actions = DatabaseQuickActions(self.config, self._manager())
        return self.quick_actions
