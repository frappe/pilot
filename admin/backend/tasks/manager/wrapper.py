"""
Entry point for a forked child process that runs a bench command.

Invoked as: python -m admin.backend.tasks.manager.wrapper <task-dir>

This module uses only the standard library — no cli imports.
"""

import json
import pickle
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def callback_handler(callback_bin_path: Path, output_log: Path, meta: dict) -> None:
    callback = pickle.loads(callback_bin_path.read_bytes())
    callback_bin_path.unlink()
    # Append in binary to match the command's own byte writes; text mode would
    # translate newlines and break TaskReader.read_output's newline split.
    with open(output_log, "ab") as log_file:
        try:
            callback(meta)
            log_file.write(b"\nCallback successfully triggered")
        except Exception as error:
            log_file.write(f"\nCallback failed: {error!s}\n".encode())


def _resolve_outcome(returncode: int, cancelled: bool) -> tuple[bool, str]:
    """Success is decided by the child's exit alone, so a cancel racing in after a
    clean exit-0 never tears down a completed install. cancelled only labels a
    non-zero exit as killed vs failed."""
    succeeded = returncode == 0
    status = "success" if succeeded else "killed" if cancelled else "failed"
    return succeeded, status


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

    cancelled = {"flag": False}
    holder: dict[str, subprocess.Popen] = {}

    # Register before Popen so a cancel (TaskRunner.kill SIGTERMs us) can't slip
    # through to the default handler and skip cleanup. ponytail: SIGTERM only; a
    # SIGKILL escalation upstream can't be cleaned after.
    def _on_term(_signum, _frame) -> None:
        cancelled["flag"] = True
        if proc := holder.get("proc"):
            proc.terminate()

    signal.signal(signal.SIGTERM, _on_term)

    with open(task_dir / "output.log", "wb") as log_file:
        process = subprocess.Popen(
            meta["command_argv"],
            cwd=cwd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        holder["proc"] = process
        returncode = process.wait()

    succeeded, status = _resolve_outcome(returncode, cancelled["flag"])

    if succeeded and on_success_bin.exists():
        callback_handler(on_success_bin, task_dir / "output.log", meta=meta)
    elif not succeeded and on_failure_bin.exists():
        callback_handler(on_failure_bin, task_dir / "output.log", meta=meta)

    for leftover in (on_success_bin, on_failure_bin):
        if leftover.exists():
            leftover.unlink()

    meta["finished_at"] = datetime.now(timezone.utc).isoformat()
    meta["exit_code"] = returncode
    (task_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    (task_dir / "status").write_text(status)


if __name__ == "__main__":
    main()
