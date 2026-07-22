from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from enum import Enum, auto

try:
    from typing import override  # type: ignore[attr-defined]  # Python 3.12+
except ImportError:  # Python 3.11

    def override(func):
        return func


from pilot.managers.processes.local import ProcessDefinition, ProcessManager


class UnitGroup(Enum):
    WORKLOAD = auto()  # web, socketio, workers, redis - what `bench stop` stops
    ADMIN = auto()  # the control plane - survives `bench stop`
    WEB = auto()  # just web, for reload_workers(web_only=True)


class ServiceRenderer(ABC):
    """Renders a ProcessDefinition to backend config text."""

    def __init__(self, bench_name: str) -> None:
        self.bench_name = bench_name

    @abstractmethod
    def render(self, pd: ProcessDefinition) -> str:
        """Render one process definition to its config-file text."""


class ManagedProcessManager(ProcessManager, ABC):
    """Base lifecycle for production process managers."""

    @abstractmethod
    def write_config(self) -> None: ...

    @abstractmethod
    def install_config(self) -> None: ...

    @abstractmethod
    def reload_manager_config(self) -> None: ...

    @abstractmethod
    def ensure_ready(self) -> None: ...

    @abstractmethod
    def apply_unit_action(self, action: str, role: UnitGroup) -> None: ...

    @abstractmethod
    def are_units_running(self, role: UnitGroup) -> bool: ...

    @override
    def start(self) -> None:
        self.write_config()
        self.ensure_ready()
        self.apply_unit_action("start", UnitGroup.ADMIN)
        self.apply_unit_action("start", UnitGroup.WORKLOAD)

    @override
    def start_workload(self) -> None:
        self.write_config()
        self.ensure_ready()
        self.apply_unit_action("start", UnitGroup.WORKLOAD)

    @override
    def stop(self) -> None:
        self.apply_unit_action("stop", UnitGroup.WORKLOAD)

    @override
    def stop_admin(self) -> None:
        self.apply_unit_action("stop", UnitGroup.ADMIN)

    @override
    def restart(self) -> None:
        self.apply_unit_action("restart", UnitGroup.WORKLOAD)

    @override
    def restart_admin(self) -> None:
        self.apply_unit_action("restart", UnitGroup.ADMIN)

    @override
    def is_running(self) -> bool:
        return self.are_units_running(UnitGroup.WORKLOAD)

    @override
    def is_admin_running(self) -> bool:
        return self.are_units_running(UnitGroup.ADMIN)

    @override
    def reload_workers(self, web_only: bool = False) -> None:
        self._invalidate_assets_cache()
        if self.is_running():
            self.apply_unit_action("restart", UnitGroup.WEB if web_only else UnitGroup.WORKLOAD)

    def start_admin(self) -> None:
        self.bench.logs_path.mkdir(parents=True, exist_ok=True)
        self.write_config()
        self.ensure_ready()
        self.apply_unit_action("start", UnitGroup.ADMIN)

    def _invalidate_assets_cache(self) -> None:
        cache_port = self.bench.config.redis.cache_port
        subprocess.run(
            ["redis-cli", "-p", str(cache_port), "del", "assets_json"],
            capture_output=True,
            timeout=5,
        )
