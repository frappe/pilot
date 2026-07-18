from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

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

        infos = []
        for path in sorted(logs_dir.glob("*.log")):
            stat = path.stat()
            infos.append(LogFileInfo(
                filename=path.name,
                size_bytes=stat.st_size,
                last_modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                process_name=path.stem,
                line_count=self.count_lines(path),
            ))
        return infos

    def tail_file(self, filename: str, lines: int = 200) -> list[str]:
        log_path = self._validated_path(filename)
        if not log_path.exists():
            raise FileNotFoundError(f"Log file not found: {filename}")

        tail = _read_tail_text(log_path, max(lines, 0)).splitlines()
        return [_ANSI_RE.sub("", line) for line in tail[-lines:]] if lines > 0 else []

    def get_file_path(self, filename: str) -> Path:
        return self._validated_path(filename)

    def follow_file(self, filename: str) -> Generator[str, None, None]:
        """Yield new lines as they're written, like `tail -f`."""
        log_path = self._validated_path(filename)
        log_path.touch()
        yielded = 0

        with open(log_path, "r", errors="replace") as file_handle:
            file_handle.seek(0, 2)  # seek to end
            while yielded < _MAX_STREAM_LINES:
                line = file_handle.readline()
                if line:
                    yield _ANSI_RE.sub("", line.rstrip("\n"))
                    yielded += 1
                else:
                    time.sleep(0.2)

    @staticmethod
    def count_lines(path: Path) -> int:
        try:
            output = subprocess.check_output(["wc", "-l", str(path)])
            return int(output.split()[0])
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
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
