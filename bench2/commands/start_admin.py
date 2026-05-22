from __future__ import annotations

import os
import subprocess
import sys
from subprocess import DEVNULL
from typing import TYPE_CHECKING

import click

from bench2.exceptions import BenchError

if TYPE_CHECKING:
    from bench2.core.bench import Bench

class StartAdminCommand:
    def __init__(self, bench: "Bench", port: int | None = None) -> None:
        self.bench = bench
        self.port = port if port is not None else bench.config.admin.port

    @property
    def _pid_file(self):
        return self.bench.pids_path / "admin.pid"

    @property
    def _port_file(self):
        return self.bench.pids_path / "admin.port"

    def run(self) -> None:
        self._check_not_already_running()
        proc = self._spawn()
        self._write_state(proc.pid)
        timeout_minutes = self.bench.config.admin.timeout // 60
        click.echo(f"Admin UI started at http://127.0.0.1:{self.port}/")
        click.echo(f"Will auto-stop after {timeout_minutes} minutes of inactivity.")

    def _check_not_already_running(self) -> None:
        if not self._pid_file.exists():
            return
        pid = int(self._pid_file.read_text().strip())
        try:
            os.kill(pid, 0)  # signal 0 = existence check only
        except ProcessLookupError:
            # Stale PID file — clean up and allow start
            self._pid_file.unlink(missing_ok=True)
            self._port_file.unlink(missing_ok=True)
            return
        saved_port = self._port_file.read_text().strip() if self._port_file.exists() else str(self.port)
        raise BenchError(f"Admin is already running at http://127.0.0.1:{saved_port}/")

    def _spawn(self) -> subprocess.Popen:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "bench2.admin.server",
                "--bench-root",
                str(self.bench.path),
                "--port",
                str(self.port),
                "--timeout",
                str(self.bench.config.admin.timeout),
            ],
            start_new_session=True,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )

    def _write_state(self, pid: int) -> None:
        self.bench.pids_path.mkdir(parents=True, exist_ok=True)
        self._pid_file.write_text(str(pid))
        self._port_file.write_text(str(self.port))
