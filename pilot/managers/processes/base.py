from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum, auto

try:
    from typing import override  # type: ignore[attr-defined]  # Python 3.12+
except ImportError:  # Python 3.11

    def override(func):
        return func


from pilot.managers.processes.local import ProcessDefinition, ProcessManager


class UnitGroup(Enum):
    WORKLOAD = auto()  # web, socketio, workers, redis and app processes - what `pilot stop` stops
    ADMIN = auto()  # the control plane - survives `pilot stop`


class ServiceRenderer(ABC):
    """Renders a ProcessDefinition to backend config text."""

    def __init__(self, bench_name: str) -> None:
        self.bench_name = bench_name

    @abstractmethod
    def render(self, pd: ProcessDefinition) -> str:
        """Render one process definition to its config-file text."""


class ManagedProcessManager(ProcessManager, ABC):
    """Base lifecycle for production process managers."""

    @property
    @override
    def process_definitions(self) -> list[ProcessDefinition]:
        return self._prod_process_definitions()

    def check_config(self) -> None:
        """Build the configuration without writing it, so what would fail to write fails before a stop."""
        self._prod_process_definitions()

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
        """Restart what runs app code. Socketio restarts only when realtime code changed, so its
        connections stay open; redis keeps its cache and admin keeps running."""
        self._clear_frappe_cache()
        if not self.is_running():
            return

        definitions = self.process_definitions
        realtime = not web_only and self._is_realtime_stale(definitions)
        self.restart_processes([pd for pd in definitions if self._is_reloaded(pd, web_only, realtime)])
        if not web_only:
            self.bench.processes.save_realtime_folders()

    def _is_realtime_stale(self, definitions: list[ProcessDefinition]) -> bool:
        """Whether realtime code changed after the running socketio started. Lite runs realtime in web."""
        from pilot.managers.platform import get_process_start_time

        socketio = [pd for pd in definitions if pd.name == "socketio"]
        if not socketio:
            return False
        state = self.get_process_states(socketio).get("socketio")
        started = get_process_start_time(state.pid) if state and state.pid else None
        return started is None or self.bench.processes.is_realtime_changed_since(started)

    @staticmethod
    def _is_reloaded(pd: ProcessDefinition, web_only: bool, realtime: bool) -> bool:
        if pd.name == "web":
            return True
        if web_only or pd.name == "admin" or (pd.app is None and pd.name.startswith("redis")):
            return False
        return realtime or pd.name != "socketio"

    def start_admin(self) -> None:
        self.bench.logs_path.mkdir(parents=True, exist_ok=True)
        self.write_config()
        self.ensure_ready()
        self.apply_unit_action("start", UnitGroup.ADMIN)
