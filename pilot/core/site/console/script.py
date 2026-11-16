from __future__ import annotations

import contextlib
import os
import select
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from pilot.exceptions import ScriptBusyError
from pilot.internal.atomic_file import exclusive_file_lock

if TYPE_CHECKING:
    from pilot.core.site import Site

MAX_SCRIPT_BYTES = 64 * 1024
TIMEOUT_SECONDS = 300
HEARTBEAT_SECONDS = 5


class SiteScript:
    """Python run with Frappe connected to a site, in its own process. Works while Desk is down."""

    # Reads the script before importing frappe, so a large script never fills the stdin pipe.
    # A read-only run sets Frappe's own read-only flag, so Frappe skips its side writes and starts
    # read-only transactions, makes the database session read-only, and blocks `frappe.db.commit()`.
    bootstrap = """\
import sys
code = sys.stdin.read()
import frappe
frappe.init(site=sys.argv[1], sites_path=".")
frappe.connect()
commit = sys.argv[2] == "commit"
if not commit:
    frappe.flags.read_only = True
    frappe.db.sql({
        "mariadb": "SET SESSION TRANSACTION READ ONLY",
        "postgres": "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
        "sqlite": "PRAGMA query_only = 1",
    }[frappe.db.db_type])
    frappe.db.commit()

    def refuse_commit(*args, **kwargs):
        raise RuntimeError("This run is read-only, so commit is blocked. Switch to Read/Write to save changes.")

    frappe.db.commit = refuse_commit
try:
    exec(compile(code, "<console>", "exec"), {"__name__": "__console__", "frappe": frappe})
    if commit:
        frappe.db.commit()
    else:
        frappe.db.rollback()
finally:
    frappe.destroy()
"""

    def __init__(self, site: "Site", code: str, commit: bool = False) -> None:
        if not code.strip():
            raise ValueError("Script is empty.")
        if len(code.encode()) > MAX_SCRIPT_BYTES:
            raise ValueError(f"Script is larger than {MAX_SCRIPT_BYTES // 1024} KB.")
        self.site = site
        self.code = code
        self.commit = commit
        self.exit_code: int | None = None
        self.is_timed_out = False

    def run(self, should_stop: Callable[[], bool] = lambda: False) -> Iterator[bytes | None]:
        """Yield output chunks, or None as a heartbeat, until the script exits. The script is
        killed on timeout, when `should_stop` turns true, or when the caller closes the iterator."""
        try:
            with exclusive_file_lock(self.site.bench.config_path / "console", blocking=False):
                process = self._start()
                try:
                    yield from self._read(process, should_stop)
                finally:
                    self.exit_code = _kill_process_group(process)
        except BlockingIOError as error:
            raise ScriptBusyError("Another script is running on this bench.") from error

    def _start(self) -> subprocess.Popen:
        """Start the script process in commit or rollback mode."""
        mode = "commit" if self.commit else "rollback"
        process = subprocess.Popen(
            [*self.site.bench.python_argv, "-c", self.bootstrap, self.site.config.name, mode],
            cwd=self.site.bench.sites_path,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        assert process.stdin is not None
        # A bootstrap that dies early closes the pipe; its output explains why.
        with contextlib.suppress(BrokenPipeError):
            process.stdin.write(self.code.encode())
        with contextlib.suppress(BrokenPipeError):
            process.stdin.close()
        return process

    def _read(self, process: subprocess.Popen, should_stop: Callable[[], bool]) -> Iterator[bytes | None]:
        """Yield the script output; yield None to keep the stream alive."""
        assert process.stdout is not None
        deadline = time.monotonic() + TIMEOUT_SECONDS
        last_yield = time.monotonic()
        while not should_stop():
            if time.monotonic() > deadline:
                self.is_timed_out = True
                return
            ready, _, _ = select.select([process.stdout], [], [], 0.5)
            if ready:
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    return
                yield chunk
                last_yield = time.monotonic()
            elif time.monotonic() - last_yield >= HEARTBEAT_SECONDS:
                yield None
                last_yield = time.monotonic()


def _kill_process_group(process: subprocess.Popen) -> int:
    """Kill the script and anything it started; return its exit code."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    assert process.stdout is not None
    process.stdout.close()
    return process.wait()
