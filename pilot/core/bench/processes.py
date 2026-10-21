from __future__ import annotations

import shlex
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pilot.core.app.processes import AppProcessError
from pilot.exceptions import BenchError
from pilot.managers.processes.definitions import ProcessState
from pilot.managers.processes.local import ProcessManager

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pilot.core.app import App
    from pilot.core.bench import Bench
    from pilot.managers.processes.base import ManagedProcessManager
    from pilot.managers.processes.definitions import ProcessDefinition

BENCH = "bench"
REALTIME_FOLDERS = "realtime-folders.txt"
STOPPED = ProcessState("stopped")


@dataclass(frozen=True)
class ProcessRow:
    name: str
    app: str  # BENCH for the bench's own processes
    state: ProcessState
    command: str


class BenchProcesses:
    """The bench's processes, and the app processes that follow the apps its sites use."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    @property
    def active_apps(self) -> set[str]:
        """Module names of the apps at least one site uses."""
        return {app for site in self.bench.sites() for app in site.active_apps()}

    def get_declared(self, app_name: str | None = None) -> dict["App", list["ProcessDefinition"]]:
        """Each app's declared processes, whether or not a site uses the app."""
        apps = [self.bench.app(app_name)] if app_name else self.bench.apps()
        declared = {app: app.processes.get_definitions() for app in apps}
        return {app: definitions for app, definitions in declared.items() if definitions}

    def get_app_definitions(self) -> list["ProcessDefinition"]:
        """The app processes that run: declared by an app a site uses, and not disabled.
        A broken declaration fails only when a site uses its app, so removing that app still works."""
        declared: dict[App, list[ProcessDefinition]] = {}
        broken: dict[App, AppProcessError] = {}
        for app in self.bench.apps():
            try:
                declared[app] = app.processes.get_definitions()
            except AppProcessError as error:
                broken[app] = error
        if not broken and not any(declared.values()):
            return []

        active = self.active_apps
        for app, failure in broken.items():
            if app.module_name in active:
                raise failure
        return self.get_running(declared, active)

    def get_running(
        self, declared: dict["App", list["ProcessDefinition"]], active: set[str] | None = None
    ) -> list["ProcessDefinition"]:
        """Those of `declared` that run. `active` defaults to the apps the sites use."""
        if not any(declared.values()):
            return []

        active = self.active_apps if active is None else active
        disabled = set(self.bench.config.processes.disabled)
        return [
            definition
            for app, definitions in declared.items()
            if app.module_name in active
            for definition in definitions
            if definition.name not in disabled
        ]

    def has_declarations(self, app_names: "Iterable[str]") -> bool:
        """Whether any of these apps, by app or module name, declares a process. Parses only those apps.
        A broken declaration counts, so applying the process set reports it instead of blocking a removal."""
        names = set(app_names)
        apps = [app for app in self.bench.apps() if app.config.name in names or app.module_name in names]
        for app in apps:
            try:
                if app.processes.get_definitions():
                    return True
            except AppProcessError:
                return True
        return False

    @property
    def realtime_folders(self) -> list[Path]:
        """Each app's realtime handler folders: `realtime/` for Node, `<module>/realtime/` for Python."""
        folders = [(app.path / "realtime", app.path / app.module_name / "realtime") for app in self.bench.apps()]
        return [folder for pair in folders for folder in pair if folder.is_dir()]

    def get_realtime_change_time(self) -> float:
        """Latest change to the code the socketio server loads: frappe's server and each app's handlers.
        Folders count too, since removing a file changes only its folder."""
        paths = [self.bench.apps_path / "frappe" / name for name in ("socketio.js", "node_utils.js")]
        for folder in self.realtime_folders:
            paths += [folder, *(path for path in folder.rglob("*") if "__pycache__" not in path.parts)]
        return max((path.stat().st_mtime for path in paths if path.exists()), default=0.0)

    def is_realtime_changed_since(self, started: float) -> bool:
        """Whether the code socketio loads changed after `started`. A removed folder leaves no mtime,
        so the folders saved after the last reload are compared too."""
        folders = "".join(f"{folder}\n" for folder in self.realtime_folders)
        record = self.bench.config_path / REALTIME_FOLDERS
        seen = record.read_text() if record.is_file() else folders
        return seen != folders or self.get_realtime_change_time() > started

    def save_realtime_folders(self) -> None:
        """Record the folders a running socketio has loaded, once a reload restarted it or found it current."""
        folders = "".join(f"{folder}\n" for folder in self.realtime_folders)
        (self.bench.config_path / REALTIME_FOLDERS).write_text(folders)

    def apply(self) -> None:
        """Write, link and start the production process set. Running processes keep running;
        a new app process starts, and a removed one stops and is unlinked."""
        from pilot.managers.processes.base import UnitGroup

        if not self.bench.config.production.enabled:
            return
        manager = cast("ManagedProcessManager", ProcessManager.for_bench(self.bench))
        if not manager.is_configured():
            return

        # Read first: supervisor's update starts the programs of a group it newly loads.
        is_running = manager.is_running()
        manager.write_config()
        manager.install_config()
        manager.reload_manager_config()
        manager.apply_unit_action("start" if is_running else "stop", UnitGroup.WORKLOAD)

    def restart(self, names: list[str]) -> list[str]:
        """Restart the named processes. An app name stands for every process of that app that runs."""
        manager = ProcessManager.for_bench(self.bench)
        definitions = self._resolve(names, manager.process_definitions)
        manager.restart_processes(definitions)
        return [definition.name for definition in definitions]

    def disable(self, names: list[str]) -> list[str]:
        """Stop app processes and keep them off across restarts, updates and installs."""
        disabled = [definition.name for definition in self._resolve(names, self._declared_definitions())]
        config = self.bench.config.processes
        config.disabled = sorted(set(config.disabled) | set(disabled))
        self.bench.config.write(self.bench.path)
        self.apply()
        return disabled

    def enable(self, names: list[str]) -> list[str]:
        """Undo disable. The process starts again if a site uses its app."""
        enabled = [definition.name for definition in self._resolve(names, self._declared_definitions())]
        config = self.bench.config.processes
        config.disabled = [name for name in config.disabled if name not in enabled]
        self.bench.config.write(self.bench.path)
        self.apply()
        return enabled

    def get_rows(self, app_name: str | None = None) -> list[ProcessRow]:
        """Each process with its state. A declared process that does not run says why."""
        manager = ProcessManager.for_bench(self.bench)
        bench_definitions = [] if app_name else [d for d in manager.process_definitions if d.app is None]
        declared = self.get_declared(app_name)
        running = [*bench_definitions, *self.get_running(declared)]
        states = manager.get_process_states(running)

        rows = []
        for definition in [*bench_definitions, *chain.from_iterable(declared.values())]:
            is_running = definition in running
            state = states.get(definition.name, STOPPED) if is_running else self._idle_state(definition.name)
            rows.append(ProcessRow(definition.name, definition.app or BENCH, state, shlex.join(definition.argv)))
        return rows

    def _idle_state(self, name: str) -> ProcessState:
        """Why a declared process does not run."""
        if name in self.bench.config.processes.disabled:
            return ProcessState("disabled")
        return ProcessState("no site uses the app")

    def _declared_definitions(self, app_name: str | None = None) -> list["ProcessDefinition"]:
        return list(chain.from_iterable(self.get_declared(app_name).values()))

    @staticmethod
    def _resolve(names: list[str], definitions: list["ProcessDefinition"]) -> list["ProcessDefinition"]:
        """The definitions `names` select, where an app name stands for each of its processes."""
        resolved: list[ProcessDefinition] = []
        for name in names:
            matches = [d for d in definitions if name in (d.name, d.app or BENCH)]
            if not matches:
                known = ", ".join(sorted(d.name for d in definitions)) or "none"
                raise BenchError(f"No process or app named '{name}'. Processes: {known}.")
            resolved += [match for match in matches if match not in resolved]
        return resolved
