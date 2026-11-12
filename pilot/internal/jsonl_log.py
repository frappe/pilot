"""Append-only JSONL log sharded by ISO week."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from pilot.internal.private_files import PrivateFile


class JsonlLog:
    """One record per line, one file per ISO week. Reads newest first without
    loading a whole shard, so a long-lived bench stays cheap to page through."""

    def __init__(self, directory: Path, prefix: str) -> None:
        self.directory = Path(directory)
        self.prefix = prefix
        self._file_pattern = re.compile(rf"^{re.escape(prefix)}_\d{{4}}_\d{{2}}\.jsonl$")

    def append(self, record: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with PrivateFile(self.current_file).open("a") as handle:
            handle.write(json.dumps(record) + "\n")

    def read_newest_first(self) -> Iterator[dict]:
        """Skips lines that are not valid JSON - a truncated write loses one record,
        not the whole feed."""
        for path in self.shards:
            for line in self._reversed_lines(path):
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    @property
    def current_file(self) -> Path:
        year, week, _ = datetime.now(UTC).isocalendar()
        return self.directory / f"{self.prefix}_{year}_{week:02d}.jsonl"

    @property
    def shards(self) -> list[Path]:
        """Newest shard first. Week numbers are zero-padded, so name sort == time sort."""
        if not self.directory.is_dir():
            return []
        files = [path for path in self.directory.iterdir() if self._file_pattern.match(path.name)]
        return sorted(files, key=lambda path: path.name, reverse=True)

    @staticmethod
    def _reversed_lines(path: Path, chunk_size: int = 65536) -> Iterator[str]:
        """Yield non-empty lines newest first without loading the whole file."""
        with path.open("rb") as handle:
            handle.seek(0, 2)
            pointer = handle.tell()
            tail = b""
            while pointer > 0:
                step = min(chunk_size, pointer)
                pointer -= step
                handle.seek(pointer)
                lines = (handle.read(step) + tail).split(b"\n")
                tail = lines.pop(0)  # may be a partial line completed by the next (earlier) chunk
                for line in reversed(lines):
                    if line:
                        yield line.decode()
            if tail:
                yield tail.decode()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
