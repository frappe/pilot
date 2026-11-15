from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from pilot.core.bench import Bench
from pilot.managers.processes.definitions import ProcessDefinitionBuilder
from pilot.managers.processes.local import ProcessManager


def check_processes(settings: dict) -> list[dict]:
    bench = Bench(Path(settings["path"]))
    if bench.is_lite_mode:
        return [
            finding(role, "web", "skipped", "integrated_lite", {"integrated_into": "web"})
            for role in ("worker", "scheduler")
        ]
    builder = ProcessDefinitionBuilder(bench, bench.python, False)
    pooled = bench.config.production.enabled and bench.config.production.process_manager == "systemd"
    if pooled:
        definitions = builder.worker_pool_definitions()
    else:
        definitions = [
            definition
            for group in bench.config.workers.groups
            for definition in builder.worker_definitions(",".join(group.queues), group.count)
        ]
        definitions.append(builder.schedule_definition())
    manager = ProcessManager.for_bench(bench)
    try:
        states = manager.get_process_states(definitions)
    except (OSError, subprocess.SubprocessError):
        return [finding("workers", bench.config.name, "unavailable", "manager_unavailable", {})]
    rows = []
    for definition in definitions:
        role = "scheduler" if definition.name == "schedule" else "worker"
        state = states.get(definition.name)
        if state is None:
            rows.append(finding(role, definition.name, "unavailable", "manager_state_missing", {}))
            continue
        details = {"state": state.state, "pid": state.pid, "manager": type(manager).__name__}
        status, reason = process_status(state.state, state.pid)
        if status == "ok" and not bench.config.production.enabled:
            status, reason = verify_command(state.pid, definition.argv[1:], bench.sites_path)
            details["identity_check"] = "command_signature_and_working_directory"
        rows.append(finding(role, definition.name, status, reason, details))
    if pooled:
        rows.append(
            finding(
                "scheduler",
                "worker_pools",
                "skipped",
                "integrated_worker_pool",
                {"integrated_into": [definition.name for definition in definitions]},
            )
        )
    return rows


def process_status(state: str, pid: int | None) -> tuple[str, str]:
    running = state.lower() in ("running", "active (running)") and bool(pid)
    return ("ok", "") if running else ("error", "process_not_running")


def verify_command(pid: int, signature: list[str], expected_directory: Path) -> tuple[str, str]:
    try:
        proc = Path("/proc")
        if proc.is_dir():
            arguments = (
                (proc / str(pid) / "cmdline").read_bytes().decode(errors="replace").strip("\0").split("\0")
            )
            directory = (proc / str(pid) / "cwd").resolve(strict=True)
        else:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False
            )
            arguments = shlex.split(result.stdout)
            location = subprocess.run(
                ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                capture_output=True,
                text=True,
                check=False,
            )
            directory = next(
                (Path(line[1:]) for line in location.stdout.splitlines() if line.startswith("n")), None
            )
            if directory is None:
                return "unavailable", "process_identity_unavailable"
    except FileNotFoundError:
        return (
            ("error", "process_not_running")
            if Path("/proc").is_dir()
            else ("unavailable", "process_identity_unavailable")
        )
    except (OSError, ValueError):
        return "unavailable", "process_identity_unavailable"
    matches = directory == expected_directory.resolve() and any(
        arguments[index : index + len(signature)] == signature for index in range(len(arguments))
    )
    return ("ok", "") if matches else ("error", "process_identity_mismatch")


def finding(role: str, target: str, status: str, reason: str, details: dict) -> dict:
    return {
        "code": "scheduler_process" if role == "scheduler" else "worker_process",
        "target": target,
        "status": status,
        "reason": reason,
        "details": details,
    }
