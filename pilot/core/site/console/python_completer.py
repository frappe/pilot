from __future__ import annotations

import contextlib
import json
import select
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.core.build_memory import available_memory_mb, can_read_memory
from pilot.exceptions import LowMemoryError

if TYPE_CHECKING:
    from pilot.core.bench import Bench

REPLY_SECONDS = 15
# A warm helper holds about 120-180 MB.
MIN_AVAILABLE_MB = 180

# Runs in the bench's Python, never in pilot's, and exits by itself after 5 minutes without a request.
HELPER = (Path(__file__).parent / "jedi_helper.py").read_text()


class PythonCompleter:
    """One long-lived Jedi process for a bench, started on first use and reused after that."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def get_completions(self, code: str, line: int, column: int, controllers: dict[str, str]) -> list[dict]:
        """Jedi completions at a 1-based line and 0-based column."""
        return self._ask({"code": code, "line": line, "column": column, "controllers": controllers}) or []

    def get_docstring(self, code: str, line: int, column: int, controllers: dict[str, str], name: str) -> str:
        """Docstring of the completion called `name` at that position."""
        request = {"code": code, "line": line, "column": column, "controllers": controllers, "name": name}
        return self._ask(request) or ""

    def _ask(self, request: dict):
        """One request to the helper. A slow or dead helper is stopped and the answer is None."""
        with self._lock:
            if not self.is_running:
                self._start()
            process = self._process
            assert process is not None and process.stdin is not None and process.stdout is not None
            try:
                process.stdin.write(json.dumps(request) + "\n")
                process.stdin.flush()
            # The helper may have just exited on its idle timeout; the next request starts a new one.
            except BrokenPipeError:
                self._stop()
                return None
            if not select.select([process.stdout], [], [], REPLY_SECONDS)[0]:
                self._stop()
                return None
            reply = process.stdout.readline()
            return json.loads(reply) if reply else None

    def _start(self) -> None:
        if can_read_memory() and available_memory_mb() < MIN_AVAILABLE_MB:
            raise LowMemoryError(f"Python suggestions need {MIN_AVAILABLE_MB} MB of free memory.")
        self._process = subprocess.Popen(
            [str(self.bench.python), "-c", HELPER],
            cwd=self.bench.sites_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )

    def _stop(self) -> None:
        if self._process is None:
            return
        with contextlib.suppress(ProcessLookupError):
            self._process.kill()
        self._process.wait()
        self._process = None
