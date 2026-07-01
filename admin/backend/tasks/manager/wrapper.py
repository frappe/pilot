"""
Entry point for a forked child process that runs a bench command.

Invoked as: python -m admin.backend.tasks.manager.wrapper <task-dir>

This module uses only the standard library — no cli imports.
"""

import json
import os
import pickle
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_HOSTNAME = socket.gethostname()

# facility=1 (user-level messages), severity=6 (informational) -> PRI 14.
_PRI = 14


def _syslog_prefix(tag: str, pid: int) -> str:
    """RFC 5424 envelope, nil MSGID/STRUCTURED-DATA: '<PRI>1 TIMESTAMP HOST APP-NAME PROCID - - '.

    This is the on-disk storage format (real syslog, so a log-shipping agent
    can ingest output.log as-is); TaskReader strips it back off before the
    message ever reaches the API or UI — end users only ever see plain text.
    """
    ts = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    return f"<{_PRI}>1 {ts} {_HOSTNAME} {tag} {pid} - - "


def run_with_syslog_output(command_argv: list[str], cwd: str, tag: str, log_path: Path) -> int:
    """Run command_argv, writing its merged stdout/stderr to log_path with a
    syslog envelope on every line. \\r-terminated progress redraws get their
    own envelope too, so TaskReader's existing \\r-collapse logic still picks
    the final redraw of a line."""
    process = subprocess.Popen(command_argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    fd = process.stdout.fileno()

    with open(log_path, "wb") as log_file:
        buf = b""
        while chunk := os.read(fd, 8192):
            for byte in chunk:
                ch = bytes([byte])
                if ch in (b"\n", b"\r"):
                    log_file.write(_syslog_prefix(tag, process.pid).encode() + buf + ch)
                    buf = b""
                else:
                    buf += ch
            log_file.flush()
        if buf:
            log_file.write(_syslog_prefix(tag, process.pid).encode() + buf + b"\n")

    process.stdout.close()
    return process.wait()


def callback_handler(callback_bin_path: Path, output_log: Path, meta: dict) -> None:
    callback = pickle.loads(callback_bin_path.read_bytes())
    callback_bin_path.unlink()
    prefix = _syslog_prefix(meta["command"], os.getpid())
    with open(output_log, "a") as log_file:
        try:
            callback(meta)
            log_file.write(f"{prefix}Callback successfully triggered\n")
        except Exception as error:
            log_file.write(f"{prefix}Callback failed: {error!s}\n")


def main() -> None:
    task_dir = Path(sys.argv[1])
    meta = json.loads((task_dir / "meta.json").read_text())
    on_success_bin = task_dir / "on_success.bin"
    on_failure_bin = task_dir / "on_failure.bin"

    # frappe's bench CLI (env/bin/bench) loads apps.txt from the current
    # directory using sites_path=".", so cwd must be the sites/ subdirectory.
    bench_root = Path(meta["bench_root"])
    sites_dir = bench_root / "sites"
    cwd = str(sites_dir) if sites_dir.is_dir() else str(bench_root)

    exit_code = run_with_syslog_output(meta["command_argv"], cwd, meta["command"], task_dir / "output.log")

    if exit_code == 0 and on_success_bin.exists():
        callback_handler(on_success_bin, task_dir / "output.log", meta=meta)
    elif exit_code != 0 and on_failure_bin.exists():
        callback_handler(on_failure_bin, task_dir / "output.log", meta=meta)

    for leftover in (on_success_bin, on_failure_bin):
        if leftover.exists():
            leftover.unlink()

    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    meta["exit_code"] = exit_code
    (task_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    status = "success" if exit_code == 0 else "failed"
    (task_dir / "status").write_text(status)


if __name__ == "__main__":
    main()
