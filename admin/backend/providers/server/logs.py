from __future__ import annotations

import contextlib
import errno
import io
import os
import re
import stat
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from pilot.internal.jsonl_log import JsonLines
from pilot.managers.processes.sandbox import WORKLOAD_LOGS
from pilot.managers.task.reader import HEARTBEAT_SECONDS


@dataclass
class LogFileInfo:
    filename: str
    size_bytes: int
    last_modified: datetime
    process_name: str
    line_count: int
    has_errors: bool


class LogProvider:
    ansi_re = re.compile(r"\x1b\[[0-9;]*[mKJHfABCDGsu]")
    # Error lines from Python logging, gunicorn, nginx and tracebacks. Servers log INFO to stderr,
    # so a *.error.log file name alone says nothing.
    error_line_re = re.compile(
        r"\b(ERROR|CRITICAL|FATAL)\b|\[(error|crit|alert|emerg)\]|Traceback \(most recent call last\)"
    )

    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    @property
    def logs_dir(self) -> Path:
        """The bench's own logs folder."""
        return self._bench_root / "logs"

    def get_all(self) -> list[LogFileInfo]:
        """The log files."""
        if not self.logs_dir.exists():
            return []

        paths = [*(self.logs_dir / WORKLOAD_LOGS).glob("*.log"), *self.logs_dir.glob("*.log")]
        infos = [self.get_info(name) for name in sorted({path.name for path in paths})]
        return [info for info in infos if info is not None]

    def get_info(self, filename: str) -> LogFileInfo | None:
        """The details of a log file, or None when it cannot be opened safely."""
        try:
            handle = self.open_file(filename)
        except (ValueError, OSError):
            return None

        with handle:
            file_stat = os.fstat(handle.fileno())
            return LogFileInfo(
                filename=filename,
                size_bytes=file_stat.st_size,
                last_modified=datetime.fromtimestamp(file_stat.st_mtime, tz=UTC),
                process_name=Path(filename).stem,
                line_count=self.count_lines(handle),
                has_errors=self.has_recent_errors(handle),
            )

    def open_file(self, filename: str) -> BinaryIO:
        """Open a log file from logs/, else from the workload's logs/workload/.
        Links, pipes and anything else that is not a regular file raise ValueError."""
        if "/" in filename or "\\" in filename:
            raise ValueError(f"Invalid filename: {filename!r}")

        for folder in (self.logs_dir, self.logs_dir / WORKLOAD_LOGS):
            with contextlib.suppress(FileNotFoundError):
                return self._open_regular_file(folder / filename)
        raise FileNotFoundError(f"Log file not found: {filename}")

    def tail_file(self, filename: str, lines: int = 200) -> list[str]:
        """The last lines of a log file."""
        with self.open_file(filename) as handle:
            tail = JsonLines.read_tail_text_from(handle, max(lines, 0)).splitlines()
        return [self.ansi_re.sub("", line) for line in tail[-lines:]] if lines > 0 else []

    def follow_file(
        self, filename: str, should_stop: Callable[[], bool] = lambda: False
    ) -> Generator[str | None, None, None]:
        """Yield new lines as they're written, like `tail -f`, until `should_stop`. None is a
        heartbeat, sent when the file stays quiet for HEARTBEAT_SECONDS."""
        yielded = 0
        last_write = time.monotonic()

        with io.TextIOWrapper(self.open_or_create_file(filename), errors="replace") as file_handle:
            file_handle.seek(0, 2)  # seek to end
            while yielded < 5000 and not should_stop():
                line = file_handle.readline()
                if line:
                    yield self.ansi_re.sub("", line.rstrip("\n"))
                    yielded += 1
                    last_write = time.monotonic()
                elif time.monotonic() - last_write >= HEARTBEAT_SECONDS:
                    yield None
                    last_write = time.monotonic()
                else:
                    time.sleep(0.2)

    def open_or_create_file(self, filename: str) -> BinaryIO:
        """Open a log file, first creating it in logs/ when neither folder has it."""
        try:
            return self.open_file(filename)
        except FileNotFoundError:
            with contextlib.suppress(FileExistsError):
                (self.logs_dir / filename).touch(exist_ok=False)
            return self.open_file(filename)

    @staticmethod
    def has_recent_errors(handle: BinaryIO) -> bool:
        """Whether the end of the open log holds an error line."""
        handle.seek(max(0, handle.seek(0, 2) - (64 * 1024)))
        return bool(LogProvider.error_line_re.search(handle.read().decode(errors="replace")))

    @staticmethod
    def count_lines(handle: BinaryIO) -> int:
        """The line count of an open file."""
        handle.seek(0)
        return sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1024 * 1024), b""))

    @staticmethod
    def _open_regular_file(path: Path) -> BinaryIO:
        """Open path without following a link or blocking on a pipe, and check what was opened."""
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise ValueError(f"Refusing a linked log file: {path.name!r}") from error
            raise

        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ValueError(f"Not a regular log file: {path.name!r}")
        return os.fdopen(descriptor, "rb")
