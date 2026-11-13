from __future__ import annotations

import re
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pilot.managers.processes.sandbox import WORKLOAD_LOGS
from pilot.managers.task.reader import HEARTBEAT_SECONDS

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mKJHfABCDGsu]")

_MAX_STREAM_LINES = 5000  # cap on lines a single log tail-stream connection emits


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


class LogProvider:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def get_all(self) -> list[LogFileInfo]:
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
    def count_lines(path: Path) -> int:
        from pilot.exceptions import CommandError
        from pilot.utils import run_command

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
