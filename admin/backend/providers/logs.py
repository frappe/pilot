from __future__ import annotations

import re
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pilot.managers.task.reader import HEARTBEAT_SECONDS

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mKJHfABCDGsu]")

_MAX_STREAM_LINES = 5000  # cap on lines a single log tail-stream connection emits
# Error lines from Python logging, gunicorn, nginx and tracebacks. Servers log INFO to stderr,
# so a *.error.log file name alone says nothing.
_ERROR_LINE_RE = re.compile(
    r"\b(ERROR|CRITICAL|FATAL)\b|\[(error|crit|alert|emerg)\]|Traceback \(most recent call last\)"
)
_ERROR_SCAN_BYTES = 64 * 1024


def _read_tail_text(path: Path, min_lines: int, block_size: int = 65536) -> str:
    """Read a bounded tail window large enough for min_lines."""
    size = path.stat().st_size
    read_size = min(block_size, size)
    with path.open("rb") as handle:
        while True:
            handle.seek(size - read_size)
            chunk = handle.read(read_size)
            if read_size >= size or chunk.count(b"\n") >= min_lines:
                return chunk.decode(errors="replace")
            read_size = min(read_size * 2, size)


@dataclass
class LogFileInfo:
    filename: str
    size_bytes: int
    last_modified: datetime
    process_name: str
    line_count: int
    has_errors: bool


class LogProvider:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def get_all(self) -> list[LogFileInfo]:
        logs_dir = self._bench_root / "logs"
        if not logs_dir.exists():
            return []

        infos = []
        for path in sorted(logs_dir.glob("*.log")):
            stat = path.stat()
            infos.append(
                LogFileInfo(
                    filename=path.name,
                    size_bytes=stat.st_size,
                    last_modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    process_name=path.stem,
                    line_count=self.count_lines(path),
                    has_errors=self.has_recent_errors(path),
                )
            )
        return infos

    def tail_file(self, filename: str, lines: int = 200) -> list[str]:
        log_path = self._validated_path(filename)
        if not log_path.exists():
            raise FileNotFoundError(f"Log file not found: {filename}")

        tail = _read_tail_text(log_path, max(lines, 0)).splitlines()
        return [_ANSI_RE.sub("", line) for line in tail[-lines:]] if lines > 0 else []

    def get_file_path(self, filename: str) -> Path:
        return self._validated_path(filename)

    def follow_file(
        self, filename: str, should_stop: Callable[[], bool] = lambda: False
    ) -> Generator[str | None, None, None]:
        """Yield new lines as they're written, like `tail -f`, until `should_stop`. None is a
        heartbeat, sent when the file stays quiet for HEARTBEAT_SECONDS."""
        log_path = self._validated_path(filename)
        log_path.touch()
        yielded = 0
        last_write = time.monotonic()

        with open(log_path, "r", errors="replace") as file_handle:
            file_handle.seek(0, 2)  # seek to end
            while yielded < _MAX_STREAM_LINES and not should_stop():
                line = file_handle.readline()
                if line:
                    yield _ANSI_RE.sub("", line.rstrip("\n"))
                    yielded += 1
                    last_write = time.monotonic()
                elif time.monotonic() - last_write >= HEARTBEAT_SECONDS:
                    yield None
                    last_write = time.monotonic()
                else:
                    time.sleep(0.2)

    @staticmethod
    def has_recent_errors(path: Path) -> bool:
        """Whether the end of the log holds an error line."""
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, handle.seek(0, 2) - _ERROR_SCAN_BYTES))
                return bool(_ERROR_LINE_RE.search(handle.read().decode(errors="replace")))
        except OSError:
            return False

    @staticmethod
    def count_lines(path: Path) -> int:
        from pilot.exceptions import CommandError
        from pilot.internal.command import run_command

        try:
            return int(run_command(["wc", "-l", str(path)]).stdout.split()[0])
        except (OSError, CommandError, IndexError, ValueError):
            return 0

    def _validated_path(self, filename: str) -> Path:
        """Resolves filename inside logs/, rejecting separators/traversal so
        callers can't escape the log dir."""
        if "/" in filename or "\\" in filename:
            raise ValueError(f"Invalid filename: {filename!r}")

        logs_dir = (self._bench_root / "logs").resolve()
        resolved = (self._bench_root / "logs" / filename).resolve()
        if resolved.parent != logs_dir:
            raise ValueError(f"Path traversal detected in filename: {filename!r}")

        return resolved
