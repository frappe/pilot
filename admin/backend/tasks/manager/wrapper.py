"""
Entry point for a forked child process that runs a bench command.

Invoked as: python -m admin.backend.tasks.manager.wrapper <task-dir>

This module uses only the standard library and the fixed callback registry.
"""

import base64
import json
import os
import socket
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from admin.backend.tasks.callbacks import run_callback
from admin.backend.tasks.manager.task_state import (
    TERMINAL_TASK_STATUSES,
    TaskStatus,
    parse_task_status,
    validate_task_transition,
)
from pilot.secure_files import open_private, write_private_text

_HOSTNAME = socket.gethostname()

# facility=1 (user-level messages), severity=6 (informational) -> PRI 14.
_PRI = 14


def _syslog_prefix_parts(tag: str, pid: int) -> tuple[bytes, bytes]:
    """Envelope split around the only field that changes per line (TIMESTAMP),
    so callers format just a timestamp instead of rebuilding the whole prefix."""
    head = f"<{_PRI}>1 ".encode()
    tail = f" {_HOSTNAME} {tag} {pid} - - ".encode()
    return head, tail


def _redact(data: bytes, redactions: list[bytes]) -> bytes:
    for secret in redactions:
        data = data.replace(secret, b"[redacted]")
    return data


def run_with_syslog_output(
    command_argv: list[str],
    cwd: str,
    tag: str,
    log_path: Path,
    redactions: list[str] | None = None,
) -> int:
    """Run command_argv, writing its merged stdout/stderr to log_path with a
    syslog envelope on every line. \\r-terminated progress redraws get their
    own envelope too, so TaskReader's existing \\r-collapse logic still picks
    the final redraw of a line.

    Delimiters are located with bytes.find() (C-speed scan) rather than a
    Python for-loop over every byte, so long delimiter-free runs (e.g. a
    single long `frappe build` log line) cost one slice-copy instead of one
    interpreter iteration per byte.
    """
    process = subprocess.Popen(command_argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    fd = process.stdout.fileno()
    head, tail = _syslog_prefix_parts(tag, process.pid)
    secret_bytes = sorted({value.encode() for value in redactions or [] if value}, key=len, reverse=True)

    def write_prefix(log_file) -> None:
        ts = datetime.now(timezone.utc).isoformat(timespec="microseconds").encode()
        log_file.write(head)
        log_file.write(ts)
        log_file.write(tail)

    with open_private(log_path, "wb") as log_file:
        buf = bytearray()
        while chunk := os.read(fd, 65536):
            start = 0
            chunk_len = len(chunk)
            while start < chunk_len:
                nl = chunk.find(b"\n", start)
                cr = chunk.find(b"\r", start)
                if nl == -1 and cr == -1:
                    buf += chunk[start:]
                    break
                idx = nl if cr == -1 or (nl != -1 and nl < cr) else cr
                write_prefix(log_file)
                log_file.write(_redact(bytes(buf) + chunk[start:idx], secret_bytes))
                log_file.write(chunk[idx:idx + 1])
                buf.clear()
                start = idx + 1
            log_file.flush()
        if buf:
            write_prefix(log_file)
            log_file.write(_redact(bytes(buf), secret_bytes))
            log_file.write(b"\n")

    process.stdout.close()
    return process.wait()


def callback_handler(
    callback: dict,
    output_log: Path,
    meta: dict,
    redactions: list[str] | None = None,
) -> None:
    head, tail = _syslog_prefix_parts(meta["command"], os.getpid())
    ts = datetime.now(timezone.utc).isoformat(timespec="microseconds").encode()
    prefix = (head + ts + tail).decode()
    with open_private(output_log, "a") as log_file:
        try:
            run_callback(callback, meta)
            log_file.write(f"{prefix}Callback successfully triggered\n")
        except Exception as error:
            message = str(error)
            for secret in redactions or []:
                message = message.replace(secret, "[redacted]")
            log_file.write(f"{prefix}Callback failed: {message}\n")


def _secret_values(value, key: str = "") -> list[str]:
    sensitive = any(
        marker in key.lower()
        for marker in ("password", "secret", "token", "credential", "access_key", "private_key")
    )
    if sensitive and isinstance(value, (str, int, float)):
        return [str(value)] if str(value) else []
    if isinstance(value, dict):
        return [
            secret
            for child_key, child in value.items()
            for secret in _secret_values(child, child_key)
        ]
    if isinstance(value, list):
        return [secret for child in value for secret in _secret_values(child, key)]
    return []


def _load_redactions(task_dir: Path, bench_root: Path) -> list[str]:
    values = []
    secret_path = task_dir / "secrets.json"
    if secret_path.exists():
        values.extend(_secret_values(json.loads(secret_path.read_text())))
    config_path = bench_root / "bench.toml"
    if config_path.exists():
        try:
            with config_path.open("rb") as config_file:
                values.extend(_secret_values(tomllib.load(config_file)))
        except (OSError, tomllib.TOMLDecodeError):
            pass
    git_config_path = bench_root / ".bench.git.info"
    if git_config_path.exists():
        try:
            values.extend(_secret_values(json.loads(git_config_path.read_text())))
        except (OSError, ValueError):
            pass
    expanded = list(values)
    for value in values:
        expanded.append(base64.b64encode(value.encode()).decode())
        for username in ("x-access-token", "oauth2"):
            basic = base64.b64encode(f"{username}:{value}".encode()).decode()
            expanded.append(basic)
    return list(dict.fromkeys(expanded))


def main() -> None:
    task_dir = Path(sys.argv[1])
    meta = json.loads((task_dir / "meta.json").read_text())
    current_status = parse_task_status((task_dir / "status").read_text().strip())
    if current_status in TERMINAL_TASK_STATUSES:
        return
    if current_status == TaskStatus.QUEUED:
        validate_task_transition(current_status, TaskStatus.RUNNING)
        meta["started_at"] = datetime.now(timezone.utc).isoformat()
        write_private_text(task_dir / "meta.json", json.dumps(meta, indent=2))
        write_private_text(task_dir / "status", TaskStatus.RUNNING.value)
    callbacks_path = task_dir / "callbacks.json"
    callbacks = {}
    if callbacks_path.exists():
        try:
            callbacks = json.loads(callbacks_path.read_text())
        except (OSError, ValueError):
            invalid = {"operation": "invalid-callback-json", "args": {}}
            callbacks = {"on_success": invalid, "on_failure": invalid}

    # frappe's bench CLI (env/bin/bench) loads apps.txt from the current
    # directory using sites_path=".", so cwd must be the sites/ subdirectory.
    bench_root = Path(meta["bench_root"])
    sites_dir = bench_root / "sites"
    cwd = str(sites_dir) if sites_dir.is_dir() else str(bench_root)

    redactions = _load_redactions(task_dir, bench_root)
    try:
        exit_code = run_with_syslog_output(
            meta["command_argv"],
            cwd,
            meta["command"],
            task_dir / "output.log",
            redactions,
        )
    finally:
        (task_dir / "secrets.json").unlink(missing_ok=True)

    selected = callbacks.get("on_success" if exit_code == 0 else "on_failure")
    if selected:
        callback_handler(selected, task_dir / "output.log", meta=meta, redactions=redactions)

    for leftover in (
        callbacks_path,
        task_dir / "on_success.bin",
        task_dir / "on_failure.bin",
    ):
        if leftover.exists():
            leftover.unlink()

    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    meta["exit_code"] = exit_code
    write_private_text(task_dir / "meta.json", json.dumps(meta, indent=2))
    status = TaskStatus.SUCCESS if exit_code == 0 else TaskStatus.FAILED
    validate_task_transition(TaskStatus.RUNNING, status)
    write_private_text(task_dir / "status", status.value)


if __name__ == "__main__":
    main()
