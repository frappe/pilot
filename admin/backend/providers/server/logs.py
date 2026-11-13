from __future__ import annotations

import re
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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

    def get_all(self) -> list[LogFileInfo]:
        """The log files."""
        logs_dir = self._bench_root / "logs"
        if not logs_dir.exists():
            return []

        # The workload owns this folder, so a link or a pipe there is not read.
        candidates = (logs_dir / WORKLOAD_LOGS).glob("*.log")
        workload_paths = [path for path in candidates if path.is_file() and not path.is_symlink()]
        paths = {path.name: path for path in [*workload_paths, *logs_dir.glob("*.log")]}

        infos = []
        for path in sorted(paths.values(), key=lambda path: path.name):
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
        """The last lines of a log file."""
        log_path = self._validated_path(filename)
        if not log_path.exists():
            raise FileNotFoundError(f"Log file not found: {filename}")

        tail = JsonLines(log_path).read_tail_text(max(lines, 0)).splitlines()
        return [self.ansi_re.sub("", line) for line in tail[-lines:]] if lines > 0 else []

    def get_file_path(self, filename: str) -> Path:
        """The validated path of a log file."""
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

    @staticmethod
    def has_recent_errors(path: Path) -> bool:
        """Whether the end of the log holds an error line."""
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, handle.seek(0, 2) - (64 * 1024)))
                return bool(LogProvider.error_line_re.search(handle.read().decode(errors="replace")))
        except OSError:
            return False

    @staticmethod
    def count_lines(path: Path) -> int:
        """The line count of a file."""
        from pilot.exceptions import CommandError
        from pilot.internal.command import run_command

        try:
            return int(run_command(["wc", "-l", str(path)]).stdout.split()[0])
        except (OSError, CommandError, IndexError, ValueError):
            return 0

    def _validated_path(self, filename: str) -> Path:
        """Resolves filename in logs/, then in the workload's logs/workload/, rejecting
        separators/traversal so callers can't escape either folder."""
        if "/" in filename or "\\" in filename:
            raise ValueError(f"Invalid filename: {filename!r}")

        logs_dir = self._bench_root / "logs"
        pilot_path = self._resolve_inside(logs_dir, filename)
        if pilot_path.exists():
            return pilot_path

        workload_path = self._resolve_inside(logs_dir / WORKLOAD_LOGS, filename)
        return workload_path if workload_path.is_file() else pilot_path

    @staticmethod
    def _resolve_inside(folder: Path, filename: str) -> Path:
        resolved = (folder / filename).resolve()
        if resolved.parent != folder.resolve():
            raise ValueError(f"Path traversal detected in filename: {filename!r}")
        return resolved
