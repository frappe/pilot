from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

WINDOW_SECONDS = {"30m": 1800, "1h": 3600, "6h": 21600, "12h": 43200, "24h": 86400, "1w": 604800}
DEFAULT_WINDOW = "1h"


class WindowedLogProvider:
    """Shared plumbing for providers that aggregate an append-only JSON-Lines
    log over a selectable time window (30m/1h/6h/12h/24h/1w): window
    validation, a reversed-order reader that stops once past the window so a
    short window never scans the whole file, and timestamp normalization."""

    def __init__(self, window: str) -> None:
        self.window = window if window in WINDOW_SECONDS else DEFAULT_WINDOW
        self.window_seconds = WINDOW_SECONDS[self.window]
        self.cutoff = datetime.now(UTC) - timedelta(seconds=self.window_seconds)

    def now_ms(self) -> int:
        return self.to_epoch_ms(datetime.now(UTC))

    def records_in_window(self, path: Path, time_key: str = "time") -> Iterator[dict]:
        """Records newest-first, stopping once older than the window."""
        for record in self._iter_records_reversed(path):
            if not isinstance(record, dict):
                continue
            when = self.get_time(record.get(time_key))
            if when is None:
                continue
            if when < self.cutoff:
                break
            yield record

    @staticmethod
    def get_time(value: object) -> datetime | None:
        """Older lines carry naive server-local time; astimezone() normalizes it to UTC."""
        if not isinstance(value, str):
            return None
        try:
            when = datetime.fromisoformat(value)
        except ValueError:
            return None
        return when if when.tzinfo else when.astimezone(UTC)

    @staticmethod
    def to_epoch_ms(when: datetime) -> int:
        return int(when.timestamp() * 1000)

    @classmethod
    def get_earliest(cls, path: Path, time_key: str = "time") -> int | None:
        if not path.exists():
            return None
        with path.open() as handle:
            first = handle.readline()
        record = _safe_json(first.encode())
        if not isinstance(record, dict):
            return None
        when = cls.get_time(record.get(time_key))
        return cls.to_epoch_ms(when) if when else None

    @classmethod
    def _iter_records_reversed(cls, path: Path, block_size: int = 65536) -> Iterator[dict]:
        """Yields JSON records newest-first, in blocks from the end."""
        for line in cls._iter_lines_reversed(path, block_size):
            record = _safe_json(line)
            if record is not None:
                yield record

    @staticmethod
    def _iter_lines_reversed(path: Path, block_size: int = 65536) -> Iterator[bytes]:
        """Yields raw lines newest-first, in blocks from the end, so a short
        window never touches the whole file."""
        if not path.exists():
            return
        with path.open("rb") as handle:
            handle.seek(0, 2)
            position = handle.tell()
            remainder = b""
            while position > 0:
                size = min(block_size, position)
                position -= size
                handle.seek(position)
                lines = (handle.read(size) + remainder).split(b"\n")
                remainder = lines[0]
                yield from reversed(lines[1:])
            if remainder:
                yield remainder


def _safe_json(line: bytes):
    if not line.strip():
        return None
    try:
        return json.loads(line)
    except ValueError:
        return None
