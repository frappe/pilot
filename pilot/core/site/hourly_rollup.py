from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pilot.internal.atomic_file import atomic_write_private_text
from pilot.internal.jsonl_log import JsonLines

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pilot.core.bench import Bench

HOUR_FORMAT = "%Y-%m-%dT%H"


class HourlyRollup:
    """Per-site counts by UTC hour, kept from an append-only JSON-lines log.

    Each update reads only the bytes added since the last one, so readers get a
    few KB instead of scanning the whole log. A missing state reads only the retention window."""

    retention_hours = 24
    time_key = "time"
    source_name = ""
    state_name = ""

    def __init__(self, bench: Bench) -> None:
        self.source = bench.logs_path / self.source_name
        self.state_path = bench.logs_path / self.state_name

    def update(self, now: datetime | None = None) -> None:
        """Add new log records to the hourly counts and prune old hours."""
        if not self.source.exists():
            return
        now = now or datetime.now(UTC)
        state = self.read_state()
        sites = state["sites"]
        for hour, site, record in self.read_new_records(state, now):
            self.add(sites.setdefault(site, {}), hour, record)
        self.prune(sites, now)
        atomic_write_private_text(self.state_path, json.dumps(state))

    def read_new_records(
        self, state: dict, now: datetime, max_bytes: int | None = None
    ) -> Iterator[tuple[str, str, dict]]:
        """(hour, site, record) for each whole line added since `state`, which moves past them.
        A new or truncated log starts again at the window; `max_bytes` caps one call's reading."""
        stat = self.source.stat()
        is_same_log = state["inode"] == stat.st_ino and state["offset"] <= stat.st_size
        offset = max(state["offset"] if is_same_log else 0, self.get_window_offset(now))
        end = offset + max_bytes if max_bytes else stat.st_size
        state["inode"], state["offset"] = stat.st_ino, offset
        with self.source.open("rb") as log:
            log.seek(offset)
            for line in log:
                if not line.endswith(b"\n") or state["offset"] >= end:
                    break  # a line still being written is read whole by the next update
                state["offset"] += len(line)
                record = JsonLines.parse(line)
                if record is None:
                    continue
                hour, site = self.get_hour(record.get(self.time_key)), record.get("site")
                if hour and isinstance(site, str):
                    yield hour, site, record

    def get_window_offset(self, now: datetime) -> int:
        """Where the retention window starts in the log, so a first update, or one after a long
        stop, skips the older lines of a log that can be many GB."""
        oldest = self.get_hour_keys(now)[0]
        return JsonLines(self.source).find_offset(
            lambda record: (self.get_hour(record.get(self.time_key)) or "") < oldest
        )

    def add(self, hours: dict, hour: str, record: dict) -> None:
        """Add one record to the hourly counts."""
        raise NotImplementedError

    def read_state(self) -> dict:
        """The saved state, or an empty state."""
        try:
            state = json.loads(self.state_path.read_text())
        except FileNotFoundError:
            return {"inode": None, "offset": 0, "sites": {}}
        if not isinstance(state, dict) or not isinstance(state.get("sites"), dict):
            raise ValueError(f"{self.state_path} is not a rollup state.")
        return state

    def get_site_hours(self, site: str, now: datetime | None = None) -> list:
        """The site's counts for each of the last `retention_hours` hours, oldest first."""
        hours = self.read_state()["sites"].get(site, {})
        return [hours.get(hour) for hour in self.get_hour_keys(now or datetime.now(UTC))]

    def get_hour_keys(self, now: datetime) -> list[str]:
        """The hour keys in the retention window, oldest first."""
        return [
            (now - timedelta(hours=back)).strftime(HOUR_FORMAT)
            for back in range(self.retention_hours - 1, -1, -1)
        ]

    def prune(self, sites: dict, now: datetime) -> None:
        """Remove hours outside the retention window."""
        oldest = self.get_hour_keys(now)[0]
        for site in list(sites):
            sites[site] = {hour: value for hour, value in sites[site].items() if hour >= oldest}
            if not sites[site]:
                del sites[site]

    @staticmethod
    def get_hour(value: object) -> str | None:
        """Frappe writes 'YYYY-MM-DD HH:MM:SS+00:00'; older lines may be naive server time."""
        if not isinstance(value, str):
            return None
        try:
            when = datetime.fromisoformat(value)
        except ValueError:
            return None
        return (when if when.tzinfo else when.astimezone()).astimezone(UTC).strftime(HOUR_FORMAT)


class UptimeRollup(HourlyRollup):
    """[up, total] checks per hour for a week, leaving out maintenance windows."""

    retention_hours = 168
    source_name = "uptime.json.log"
    state_name = "uptime-hourly.json"

    def add(self, hours: dict, hour: str, record: dict) -> None:
        """Count an up or down ping, skipping maintenance."""
        up = record.get("up")
        if record.get("maintenance") or not isinstance(up, bool):
            return
        counts = hours.setdefault(hour, [0, 0])
        counts[0] += int(up)
        counts[1] += 1

    def get_percent(self, site: str, now: datetime | None = None) -> float | None:
        """The uptime percentage, or None when there are no pings."""
        counts = [value for value in self.get_site_hours(site, now) if value]
        total = sum(value[1] for value in counts)
        return round(sum(value[0] for value in counts) / total * 100, 2) if total else None
