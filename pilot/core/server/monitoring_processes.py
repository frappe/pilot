from __future__ import annotations

import re
import typing

from pilot.utils import run_command

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

SUPERVISOR_PROCESS_PATTERN = re.compile(
    r"^(?P<service>\S+)\s+RUNNING\s+pid\s+(?P<pid>\d+)",
    re.MULTILINE,
)
SYSTEMD_PID_PATTERN = re.compile(r"^MainPID=(?P<pid>\d+)", re.MULTILINE)


class ProcessResolver:
    def __init__(self, bench: "Bench"):
        self.bench = bench
        self.admin_service_name = f"{self.bench.config.name}-admin"

    def resolve(self) -> dict[str, int]:
        production_config = self.bench.config.production
        if not production_config.enabled:
            return {}

        manager_mapping = {
            "systemd": self.systemd_processes,
            "supervisor": self.supervisord_processes,
        }
        monitor_func = manager_mapping.get(production_config.process_manager)
        return monitor_func() if monitor_func else {}

    def systemd_processes(self) -> dict[str, int]:
        from pilot.managers.processes.systemd import SystemdProcessManager

        bench_process_manager = SystemdProcessManager(self.bench)
        systemd_dir = self.bench.config_path / "systemd"

        if not systemd_dir.exists():
            return {}

        services = [
            service.name
            for service in systemd_dir.iterdir()
            if service.name.endswith(".service") and service.name != f"{self.admin_service_name}.service"
        ]
        if not services:
            return {}

        env = bench_process_manager._systemctl_env()
        cmd = bench_process_manager._systemctl("show", "--property", "MainPID", *services)
        output = run_command(cmd, env=env).stdout.decode().strip()
        return self._systemd_pids(services, output)

    def supervisord_processes(self) -> dict[str, int]:
        from pilot.managers.processes.supervisor import SupervisorProcessManager

        bench_process_manager = SupervisorProcessManager(self.bench)
        result = run_command(
            ["supervisorctl", "-c", str(bench_process_manager.supervisor_conf_path), "status"]
        )
        supervised_processes = result.stdout.decode().strip()
        return self._supervisor_pids(supervised_processes)

    def _systemd_pids(self, services: list[str], output: str) -> dict[str, int]:
        pids = {}
        for service, match in zip(services, SYSTEMD_PID_PATTERN.finditer(output), strict=False):
            pid = int(match.group("pid"))
            if pid > 0:
                pids[service] = pid
        return pids

    def _supervisor_pids(self, output: str) -> dict[str, int]:
        pids = {}
        for match in SUPERVISOR_PROCESS_PATTERN.finditer(output):
            service_name = match.group("service")
            if service_name != self.admin_service_name:
                pids[service_name] = int(match.group("pid"))
        return pids
