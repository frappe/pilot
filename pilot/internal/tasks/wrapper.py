"""Forked task child process: python -m pilot.internal.tasks.wrapper <task-dir>."""

import json
import os
import signal
import socket
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from pilot.internal.tasks.callbacks import run_callback, trigger_for_task_status
from pilot.managers.task.models import TaskStatus
from pilot.internal.tasks.store import TaskStore
from pilot.utils import open_private

_HOSTNAME = socket.gethostname()

# facility=1 (user-level messages), severity=6 (informational) -> PRI 14.
_PRI = 14
_cancel_requested = False


def _wait_until_ready() -> bool:
    raw_fd = os.environ.pop("BENCH_TASK_READY_FD", None)
    if raw_fd is None:
        return True
    descriptor = int(raw_fd)
    try:
        return os.read(descriptor, 1) == b"1"
    finally:
        os.close(descriptor)


def _request_cancel(_signum, _frame) -> None:
    global _cancel_requested
    _cancel_requested = True


def _syslog_prefix_parts(tag: str, pid: int) -> tuple[bytes, bytes]:
    """Return static syslog prefix fragments around the timestamp field."""
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
    """Run a command and write merged output as syslog-framed lines."""
    process = subprocess.Popen(
        command_argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    assert process.stdout is not None
    fd = process.stdout.fileno()
    head, tail = _syslog_prefix_parts(tag, process.pid)
    secret_bytes = sorted(
        {value.encode() for value in redactions or [] if value}, key=len, reverse=True
    )

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
                log_file.write(chunk[idx : idx + 1])
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
    return list(dict.fromkeys(values))


def main() -> None:
    global _cancel_requested
    _cancel_requested = False
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _request_cancel)
    try:
        _run_task()
    finally:
        signal.signal(signal.SIGTERM, previous)


def _run_task() -> None:
    if not _wait_until_ready():
        return
    task_dir = Path(sys.argv[1])
    task_id = task_dir.name
    store = TaskStore(task_dir.parent.parent)
    meta = store.read_metadata(task_id)
    current_status = store.read_status(task_id)
    if current_status.is_terminal:
        return
    if current_status != TaskStatus.RUNNING:
        return
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
        store.remove_private_files(task_id, "secrets.json")

    status = _finalize_task(store, task_id, exit_code)
    trigger = trigger_for_task_status(status)
    selected = callbacks.get(trigger)
    if selected:
        callback_handler(selected, task_dir / "output.log", meta=meta, redactions=redactions)

    store.remove_private_files(
        task_id,
        "callbacks.json",
        "on_success.bin",
        "on_failure.bin",
    )


def _finalize_task(store: TaskStore, task_id: str, exit_code: int) -> TaskStatus:
    updates: dict[str, object]
    if _cancel_requested:
        target = TaskStatus.KILLED
        updates = {"finished_at": datetime.now(timezone.utc).isoformat()}
    else:
        target = TaskStatus.SUCCESS if exit_code == 0 else TaskStatus.FAILED
        updates = {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": exit_code,
            "failure": None if exit_code == 0 else {"code": "command_failed"},
        }
    store.transition(task_id, TaskStatus.RUNNING, target, updates)
    return store.read_status(task_id)


if __name__ == "__main__":
    main()
