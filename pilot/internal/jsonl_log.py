from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from pilot.internal.private_files import PrivateFile


class JsonLines:
    """A file with one JSON record per line, read from either end without loading it whole."""

    def __init__(self, path: Path, block_size: int = 65536) -> None:
        self.path = Path(path)
        self.block_size = block_size

    @property
    def first_record(self) -> dict | None:
        """The oldest record, or None when the file is missing or its first line is not a record."""
        if not self.path.exists():
            return None
        with self.path.open("rb") as handle:
            return self.parse(handle.readline())

    def read_newest_first(self) -> Iterator[dict]:
        """Every record, newest first. A line that is not a JSON object is skipped, so a
        truncated write loses one record, not the whole file."""
        for line in self.read_lines_newest_first():
            if (record := self.parse(line)) is not None:
                yield record

    def read_lines_newest_first(self) -> Iterator[bytes]:
        """Every non-empty raw line, newest first, read in blocks from the end."""
        if not self.path.exists():
            return
        with self.path.open("rb") as handle:
            position = handle.seek(0, 2)
            remainder = b""
            while position > 0:
                size = min(self.block_size, position)
                position -= size
                handle.seek(position)
                lines = (handle.read(size) + remainder).split(b"\n")
                # The first piece may be a partial line that the next, earlier block completes.
                remainder = lines.pop(0)
                yield from (line for line in reversed(lines) if line.strip())
            if remainder.strip():
                yield remainder

    def read_tail_text(self, min_lines: int) -> str:
        """The end of the file as text, at least min_lines lines long when the file has them."""
        with self.path.open("rb") as handle:
            return self.read_tail_text_from(handle, min_lines, self.block_size)

    @staticmethod
    def read_tail_text_from(handle: BinaryIO, min_lines: int, block_size: int = 65536) -> str:
        """The end of an open binary file as text, at least min_lines lines long when it has them."""
        size = handle.seek(0, 2)
        read_size = min(block_size, size)
        while True:
            handle.seek(size - read_size)
            chunk = handle.read(read_size)
            if read_size >= size or chunk.count(b"\n") >= min_lines:
                return chunk.decode(errors="replace")
            read_size = min(read_size * 2, size)

    @staticmethod
    def parse(line: bytes | str) -> dict | None:
        """The line as a JSON object, or None when it is blank, invalid or not an object."""
        try:
            record = json.loads(line)
        except ValueError:
            return None
        return record if isinstance(record, dict) else None


class JsonlLog:
    """One record per line, one file per ISO week. Reads newest first without
    loading a whole shard, so a long-lived bench stays cheap to page through."""

    def __init__(self, directory: Path, prefix: str) -> None:
        self.directory = Path(directory)
        self.prefix = prefix
        self._file_pattern = re.compile(rf"^{re.escape(prefix)}_\d{{4}}_\d{{2}}\.jsonl$")

    def append(self, record: dict) -> None:
        """Add one record to the current week's file."""
        self.directory.mkdir(parents=True, exist_ok=True)
        with PrivateFile(self.current_file).open("a") as handle:
            handle.write(json.dumps(record) + "\n")

    def read_newest_first(self) -> Iterator[dict]:
        """Every record across the weekly files, newest first."""
        for path in self.shards:
            yield from JsonLines(path).read_newest_first()

    @property
    def current_file(self) -> Path:
        """The file for the current ISO week."""
        year, week, _ = datetime.now(UTC).isocalendar()
        return self.directory / f"{self.prefix}_{year}_{week:02d}.jsonl"

    @property
    def shards(self) -> list[Path]:
        """Newest shard first. Week numbers are zero-padded, so name sort == time sort."""
        if not self.directory.is_dir():
            return []
        files = [path for path in self.directory.iterdir() if self._file_pattern.match(path.name)]
        return sorted(files, key=lambda path: path.name, reverse=True)


def utc_now() -> str:
    """The current time as an ISO 8601 string in UTC."""
    return datetime.now(UTC).isoformat()
