from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config.mariadb import MEMORY_SHARE_RANGE
from pilot.core.mariadb_memory import calculate_mariadb_memory
from pilot.exceptions import DatabaseError
from pilot.managers.database.mariadb import MariaDBManager
from pilot.managers.platform import is_linux

if TYPE_CHECKING:
    from pilot.config import BenchConfig


_MARIADB_ONLY = "This action is available only when the bench uses MariaDB."
_SQLITE_HAS_NO_SERVER = "SQLite does not have a shared database server."
_UNREACHABLE = "MariaDB is not reachable with Pilot's admin credentials."
_EXTERNAL_MARIADB = "Database actions are available only for Pilot-managed MariaDB instances."
_MEMORY_SHARE_STEP = 0.05

RestartExecutor = Callable[[Callable[[], None]], None]


class DatabaseQuickActions:
    """Capability checks and guarded host-level database operations."""

    def __init__(
        self,
        config: BenchConfig,
        manager: MariaDBManager | None = None,
    ) -> None:
        self.config = config
        self.manager = (
            manager
            if manager is not None
            else (MariaDBManager(config.mariadb) if config.db_type == "mariadb" else None)
        )

    def capabilities(self) -> dict:
        engine_reason = self._engine_reason()
        if engine_reason:
            return self._unavailable_capabilities(engine_reason)
        if self.config.mariadb.existing:
            return self._unavailable_capabilities(_EXTERNAL_MARIADB)

        manager = self._manager()
        server_reason = self._managed_server_reason()
        reachable = False if server_reason else manager.is_healthy()
        restart = self._restart_capability(server_reason)
        performance = self._performance_schema_capability(reachable, restart)
        configuration = self._configuration_capability(reachable, server_reason)
        innodb_buffer_pool = self._innodb_buffer_pool_capability(configuration)
        max_connections = self._max_connections_capability(configuration)
        memory_share = self._memory_share_capability(configuration)
        manage_binlogs = self._capability(
            not server_reason and reachable,
            server_reason or ("" if reachable else _UNREACHABLE),
        )
        return {
            "engine": self.config.db_type,
            "managed": not self.config.mariadb.existing,
            "reachable": reachable,
            "actions": {
                "restart": {**restart, "requires_restart": True},
                "performance_schema": performance,
                "innodb_buffer_pool_size": innodb_buffer_pool,
                "max_connections": max_connections,
                "memory_share": memory_share,
                "manage_binlogs": manage_binlogs,
            },
        }

    def restart(self) -> None:
        self.require_restart()
        self._manager().restart_managed_server()

    def set_performance_schema(
        self,
        enabled: bool,
        restart_executor: RestartExecutor | None = None,
    ) -> bool:
        if type(enabled) is not bool:
            raise DatabaseError("enabled must be a boolean.")
        self.require_performance_schema()
        return self._manager().set_performance_schema(enabled, restart_executor=restart_executor)

    def set_innodb_buffer_pool_size(self, size_mb: int) -> bool:
        self.require_innodb_buffer_pool_size(size_mb)
        return self._manager().set_innodb_buffer_pool_size(size_mb)

    def set_max_connections(self, max_connections: int) -> bool:
        self.require_max_connections(max_connections)
        return self._manager().set_max_connections(max_connections)

    def set_memory_share(self, memory_share: float, benches_root: Path) -> None:
        """Save the database's share of host memory for every bench and size MariaDB for it."""
        from pilot.config.common import CommonConfig

        self.require_memory_share(memory_share)
        manager = self._manager()
        with manager.database_action_lock():
            self.config.mariadb.memory_share = memory_share
            manager.tune_to_host_locked()
            self.config.mariadb.memory_high_mb = None
            self.config.mariadb.memory_max_mb = None
            with CommonConfig.open(benches_root) as common:
                common.mariadb.memory_share = memory_share
                common.mariadb.memory_high_mb = None
                common.mariadb.memory_max_mb = None

    def require_memory_share(self, memory_share: float) -> dict:
        low, high = MEMORY_SHARE_RANGE
        if type(memory_share) not in (int, float) or not low <= memory_share <= high:
            raise ValueError(f"memory_share must be between {low} and {high}.")
        return self._require_available(self._configuration_capability())

    def require_restart(self) -> dict:
        return self._require_available(self._restart_capability())

    def require_performance_schema(self) -> dict:
        return self._require_available(self._performance_schema_capability())

    def require_innodb_buffer_pool_size(self, size_mb: int) -> dict:
        if type(size_mb) is not int:
            raise ValueError("size_mb must be a whole number.")
        capability = self._innodb_buffer_pool_capability()
        self._require_available(capability)
        if size_mb < capability["min_mb"] or size_mb > capability["max_mb"]:
            raise ValueError(f"size_mb must be between {capability['min_mb']} and {capability['max_mb']} MB.")
        return capability

    def require_max_connections(self, max_connections: int) -> dict:
        if type(max_connections) is not int:
            raise ValueError("max_connections must be a whole number.")
        capability = self._max_connections_capability()
        self._require_available(capability)
        if max_connections < capability["min"] or max_connections > capability["max"]:
            raise ValueError(f"max_connections must be between {capability['min']} and {capability['max']}.")
        return capability

    def _performance_schema_capability(
        self,
        reachable: bool | None = None,
        restart: dict | None = None,
    ) -> dict:
        restart = restart or self._restart_capability()
        if not restart["available"]:
            return {
                **restart,
                "enabled": None,
                "requires_restart": True,
            }
        if not is_linux():
            return {
                **self._capability(
                    False,
                    "Performance Schema configuration is available only for Pilot-managed MariaDB on Linux.",
                ),
                "enabled": None,
                "requires_restart": True,
            }

        manager = self._manager()
        reachable = manager.is_healthy() if reachable is None else reachable
        if not reachable:
            return {
                **self._capability(False, _UNREACHABLE),
                "enabled": None,
                "requires_restart": True,
            }
        try:
            enabled = manager.performance_schema_enabled()
        except DatabaseError as exc:
            return {
                **self._capability(False, str(exc)),
                "enabled": None,
                "requires_restart": True,
            }
        return {
            **self._capability(True),
            "enabled": enabled,
            "requires_restart": True,
        }

    def _restart_capability(self, server_reason: str | None = None) -> dict:
        engine_reason = self._engine_reason()
        if engine_reason:
            return self._capability(False, engine_reason)
        if self.config.mariadb.existing:
            return self._capability(False, "Pilot cannot restart an external MariaDB server.")

        server_reason = self._managed_server_reason() if server_reason is None else server_reason
        if server_reason:
            return self._capability(False, server_reason)
        return self._capability(True)

    def _configuration_capability(
        self,
        reachable: bool | None = None,
        server_reason: str | None = None,
    ) -> dict:
        engine_reason = self._engine_reason()
        if engine_reason:
            return self._capability(False, engine_reason)
        if self.config.mariadb.existing:
            return self._capability(
                False,
                "Pilot cannot change an external MariaDB server's configuration.",
            )
        if not is_linux():
            return self._capability(
                False,
                "MariaDB configuration actions are available only on Linux.",
            )

        server_reason = self._managed_server_reason() if server_reason is None else server_reason
        if server_reason:
            return self._capability(False, server_reason)
        reachable = self._manager().is_healthy() if reachable is None else reachable
        if not reachable:
            return self._capability(False, _UNREACHABLE)
        return self._capability(True)

    def _innodb_buffer_pool_capability(self, configuration: dict | None = None) -> dict:
        configuration = configuration or self._configuration_capability()
        if not configuration["available"]:
            return self._unavailable_innodb_buffer_pool(configuration["reason"])

        manager = self._manager()
        try:
            limits = manager.variable_limits()
            current_mb = manager.innodb_buffer_pool_size_mb()
            dynamic_max_mb = manager.innodb_buffer_pool_size_max_mb()
        except DatabaseError as exc:
            return self._unavailable_innodb_buffer_pool(str(exc))
        return {
            **self._capability(True),
            "current_mb": current_mb,
            "min_mb": limits.innodb_buffer_pool_min_mb,
            "max_mb": limits.innodb_buffer_pool_max_mb,
            "recommended_mb": limits.innodb_buffer_pool_recommended_mb,
            "dynamic_max_mb": dynamic_max_mb,
            "unit": "MB",
            "requires_restart": False,
        }

    def _max_connections_capability(self, configuration: dict | None = None) -> dict:
        configuration = configuration or self._configuration_capability()
        if not configuration["available"]:
            return self._unavailable_max_connections(configuration["reason"])

        manager = self._manager()
        try:
            limits = manager.variable_limits()
            current = manager.max_connections()
        except DatabaseError as exc:
            return self._unavailable_max_connections(str(exc))
        return {
            **self._capability(True),
            "current": current,
            "min": limits.max_connections_min,
            "max": limits.max_connections_max,
            "recommended": limits.max_connections_recommended,
            "requires_restart": False,
        }

    def _memory_share_capability(self, configuration: dict | None = None) -> dict:
        configuration = configuration or self._configuration_capability()
        if not configuration["available"]:
            return self._unavailable_memory_share(configuration["reason"])
        total_mb = self._manager().host_memory_mb
        low, high = MEMORY_SHARE_RANGE
        steps = []
        # The sizing each share gives, so a client never repeats the formula.
        for index in range(round((high - low) / _MEMORY_SHARE_STEP) + 1):
            share = round(low + index * _MEMORY_SHARE_STEP, 2)
            sizing = calculate_mariadb_memory(total_mb, share)
            steps.append(
                {
                    "share": share,
                    "innodb_buffer_pool_mb": sizing.innodb_buffer_pool_mb,
                    "memory_max_mb": sizing.memory_max_mb,
                }
            )
        return {
            **self._capability(True),
            "current": self.config.mariadb.memory_share,
            "min": low,
            "max": high,
            "total_memory_mb": total_mb,
            "steps": steps,
            "requires_restart": False,
        }

    def _managed_server_reason(self) -> str:
        if self.config.mariadb.existing:
            return ""
        manager = self._manager()
        if not manager.is_installed():
            return "MariaDB is not installed on this server."
        if not manager.is_provisioned():
            return "Pilot's MariaDB server has not been provisioned."
        return ""

    def _engine_reason(self) -> str:
        if self.config.db_type == "sqlite":
            return _SQLITE_HAS_NO_SERVER
        if self.config.db_type != "mariadb":
            return _MARIADB_ONLY
        return ""

    def _unavailable_capabilities(self, reason: str) -> dict:
        unavailable = self._capability(False, reason)
        return {
            "engine": self.config.db_type,
            "managed": False,
            "reachable": False,
            "actions": {
                "restart": {**unavailable, "requires_restart": True},
                "performance_schema": {
                    **unavailable,
                    "enabled": None,
                    "requires_restart": True,
                },
                "innodb_buffer_pool_size": self._unavailable_innodb_buffer_pool(reason),
                "max_connections": self._unavailable_max_connections(reason),
                "memory_share": self._unavailable_memory_share(reason),
                "manage_binlogs": unavailable,
            },
        }

    def _manager(self) -> MariaDBManager:
        if self.manager is None:
            raise DatabaseError(self._engine_reason() or _MARIADB_ONLY)
        return self.manager

    @staticmethod
    def _capability(available: bool, reason: str = "") -> dict:
        return {"available": available, "reason": reason}

    @classmethod
    def _unavailable_innodb_buffer_pool(cls, reason: str) -> dict:
        return {
            **cls._capability(False, reason),
            "current_mb": None,
            "min_mb": None,
            "max_mb": None,
            "recommended_mb": None,
            "dynamic_max_mb": None,
            "unit": "MB",
            "requires_restart": False,
        }

    @classmethod
    def _unavailable_max_connections(cls, reason: str) -> dict:
        return {
            **cls._capability(False, reason),
            "current": None,
            "min": None,
            "max": None,
            "recommended": None,
            "requires_restart": False,
        }

    @classmethod
    def _unavailable_memory_share(cls, reason: str) -> dict:
        return {
            **cls._capability(False, reason),
            "current": None,
            "min": None,
            "max": None,
            "total_memory_mb": None,
            "steps": [],
            "requires_restart": False,
        }

    @staticmethod
    def _require_available(capability: dict) -> dict:
        if not capability["available"]:
            raise DatabaseError(capability["reason"])
        return capability
