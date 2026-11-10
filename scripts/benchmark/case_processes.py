"""Process definition building, state detection, and process listing benchmarks."""

from __future__ import annotations

from benchmark.harness import Case
from pilot.core.bench.processes import BenchProcesses
from pilot.managers.processes.definitions import ProcessDefinitionBuilder
from pilot.managers.processes.local import ProcessManager
from pilot.managers.processes.supervisor import SupervisorProcessManager
from pilot.managers.processes.systemd import SystemdProcessManager

CASES = [
    Case(
        name="processes.definitions",
        group="processes",
        description="ProcessManager.process_definitions: build the dev process set",
        fn=lambda fixture: ProcessManager.for_bench(fixture.bench()).process_definitions,
        iterations=30,
        warmup=5,
    ),
    Case(
        name="processes.prod_definitions",
        group="processes",
        description="ProcessDefinitionBuilder.prod_process_definitions: build the production process set",
        fn=lambda fixture: ProcessDefinitionBuilder(
            fixture.bench(), fixture.bench().python, fixture.bench().config.watch_admin_js
        ).prod_process_definitions(),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="processes.rows",
        group="processes",
        description="BenchProcesses.get_rows(): definitions plus per-process state for `processes ls`",
        fn=lambda fixture: BenchProcesses(fixture.bench()).get_rows(),
        iterations=20,
        warmup=3,
        profile=True,
    ),
    Case(
        name="processes.app_definitions",
        group="processes",
        description="BenchProcesses.get_app_definitions(): parse every app's process declarations",
        fn=lambda fixture: BenchProcesses(fixture.bench()).get_app_definitions(),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="processes.detect_running",
        group="processes",
        description="ProcessManager.detect_running(): systemd then supervisor activity probes",
        fn=lambda fixture: ProcessManager.detect_running(fixture.bench()),
        iterations=20,
        warmup=3,
        profile=True,
    ),
    Case(
        name="processes.local_states",
        group="processes",
        description="ProcessManager.get_process_states(): read per-process pid files",
        fn=lambda fixture: ProcessManager(fixture.bench()).get_process_states(
            ProcessManager(fixture.bench()).process_definitions
        ),
        iterations=30,
        warmup=5,
    ),
    Case(
        name="processes.is_admin_running",
        group="processes",
        description="ProcessManager.is_admin_running(): TCP connect to the admin port",
        fn=lambda fixture: ProcessManager(fixture.bench()).is_admin_running(),
        iterations=50,
        warmup=5,
    ),
    Case(
        name="processes.systemd_states",
        group="processes",
        description="SystemdProcessManager.get_process_states(): `systemctl show` for every unit",
        fn=lambda fixture: SystemdProcessManager(fixture.bench()).get_process_states(
            SystemdProcessManager(fixture.bench()).process_definitions
        ),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="processes.supervisor_states",
        group="processes",
        description="SupervisorProcessManager.get_process_states(): `supervisorctl status`",
        fn=lambda fixture: SupervisorProcessManager(fixture.bench()).get_process_states(
            SupervisorProcessManager(fixture.bench()).process_definitions
        ),
        iterations=20,
        warmup=3,
    ),
    Case(
        name="processes.realtime_change_time",
        group="processes",
        description="BenchProcesses.get_realtime_change_time(): stat realtime folders and handlers",
        fn=lambda fixture: BenchProcesses(fixture.bench()).get_realtime_change_time(),
        iterations=20,
        warmup=3,
    ),
]
