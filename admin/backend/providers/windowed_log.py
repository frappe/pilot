from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pilot.internal.jsonl_log import JsonLines

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
        """The current time in epoch milliseconds."""
        return self.to_epoch_ms(datetime.now(UTC))

    def records_in_window(self, path: Path, time_key: str = "time") -> Iterator[dict]:
        """Records newest-first, stopping once older than the window."""
        for record in JsonLines(path).read_newest_first():
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
        """The time in epoch milliseconds."""
        return int(when.timestamp() * 1000)

    @classmethod
    def get_earliest(cls, path: Path, time_key: str = "time") -> int | None:
        """When the oldest record was written, in epoch milliseconds; None when unknown."""
        record = JsonLines(path).first_record
        if record is None:
            return None
        when = cls.get_time(record.get(time_key))
        return cls.to_epoch_ms(when) if when else None
