from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.core.site.hourly_rollup import HOUR_FORMAT, HourlyRollup
from pilot.core.site.uptime_monitoring import PING_PATH
from pilot.internal.atomic_file import atomic_write_private_text, exclusive_file_lock

if TYPE_CHECKING:
    from collections.abc import Callable

    from pilot.core.bench import Bench


@dataclass
class NamedValue:
    name: str
    value: float


@dataclass
class SiteActivity:
    """One site's last 24 hours, oldest hour first. Durations are in seconds."""

    hours: list[int]
    requests: list[int]
    errors: list[int]
    jobs: list[int]
    response_ms: list[float]
    top_paths: list[NamedValue]
    failed_paths: list[NamedValue]
    slow_requests: list[NamedValue]
    slow_jobs: list[NamedValue]


class SiteActivityRollup(HourlyRollup):
    """Requests, errors and jobs of each site by UTC hour for a day, from monitor.json.log.

    Each site has its own small file, so the site Overview reads a few KB however large the
    log grows. Path lists keep the top `top_limit` per hour, so long-tail counts are approximate."""

    retention_hours = 24
    time_key = "timestamp"
    source_name = "monitor.json.log"
    state_name = "site-activity/offset.json"
    top_limit = 50
    step_bytes = 8 * 1024 * 1024
    counts = ("requests", "errors", "jobs", "duration")
    tallies = ("paths", "failed")
    peaks = ("slow_paths", "slow_jobs")

    def __init__(self, bench: Bench) -> None:
        super().__init__(bench)
        self.directory = self.state_path.parent

    def update(self, now: datetime | None = None, catch_up: bool = True) -> bool:
        """Fold new log lines into the site files, `step_bytes` at a time so memory stays flat
        however far behind it is: until caught up, or one step to keep a request quick.
        False when another update holds the lock."""
        if not self.source.exists():
            return True
        now = now or datetime.now(UTC)
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            with exclusive_file_lock(self.directory / "update", blocking=False):
                while self.fold(now) >= self.step_bytes and catch_up:
                    pass
                if catch_up:
                    self.remove_idle_sites(now)
        except BlockingIOError:
            return False
        return True

    def fold(self, now: datetime) -> int:
        """Add up to `step_bytes` of new lines to each site's hours, save the read position, and
        return how far it moved."""
        state = self.read_state()
        start = state["offset"]
        changes: dict[str, dict] = {}
        for hour, site, record in self.read_new_records(state, now, self.step_bytes):
            self.add(changes.setdefault(site, {}), hour, record)
        oldest = self.get_hour_keys(now)[0]
        for site, hours in changes.items():
            self.save_site(site, hours, oldest)
        atomic_write_private_text(
            self.state_path, json.dumps({"inode": state["inode"], "offset": state["offset"]})
        )
        return state["offset"] - start

    def remove_idle_sites(self, now: datetime) -> None:
        """Delete the files of sites with no activity in the window, such as dropped sites."""
        cutoff = now.timestamp() - self.retention_hours * 3600
        for path in self.directory.glob("*.json"):
            if path != self.state_path and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

    def read_state(self) -> dict:
        """The saved read position, or the start."""
        try:
            state = json.loads(self.state_path.read_text())
        except FileNotFoundError:
            return {"inode": None, "offset": 0}
        if not isinstance(state, dict) or not isinstance(state.get("offset"), int):
            raise ValueError(f"{self.state_path} is not a rollup state.")
        return state

    def add(self, hours: dict, hour: str, record: dict) -> None:
        """Count one request or job. Slow paths keep uptime pings, as a slow ping is a real signal."""
        duration = record.get("duration")
        if not isinstance(duration, (int, float)):
            return
        entry = hours.setdefault(hour, self.new_hour())
        if record.get("transaction_type") == "job":
            method = (record.get("job") or {}).get("method")
            entry["jobs"] += 1
            if method:
                entry["slow_jobs"][method] = max(entry["slow_jobs"].get(method, 0), duration)
            return
        request = record.get("request") or {}
        path = request.get("path")
        if record.get("transaction_type") != "request" or not path:
            return
        entry["slow_paths"][path] = max(entry["slow_paths"].get(path, 0), duration)
        status = request.get("status_code")
        if isinstance(status, int) and status >= 500:
            entry["errors"] += 1
            entry["failed"][path] = entry["failed"].get(path, 0) + 1
        if path != PING_PATH:
            entry["requests"] += 1
            entry["duration"] += duration
            entry["paths"][path] = entry["paths"].get(path, 0) + 1

    def new_hour(self) -> dict:
        """An empty hour."""
        return {**dict.fromkeys(self.counts, 0), **{key: {} for key in (*self.tallies, *self.peaks)}}

    def save_site(self, site: str, changes: dict, oldest: str) -> None:
        """Merge new hours into the site's file, dropping hours older than `oldest`."""
        path = self.get_site_path(site)
        if path is None:
            return
        hours = {hour: entry for hour, entry in self.read_site(path).items() if hour >= oldest}
        for hour, change in changes.items():
            if hour >= oldest:
                hours[hour] = self.merge(hours.get(hour) or self.new_hour(), change)
        atomic_write_private_text(path, json.dumps(hours))

    def merge(self, entry: dict, change: dict) -> dict:
        """One hour with another added to it, keeping the top names of each list."""
        for key in self.counts:
            entry[key] += change[key]
        for key in self.tallies:
            for name, count in change[key].items():
                entry[key][name] = entry[key].get(name, 0) + count
        for key in self.peaks:
            for name, duration in change[key].items():
                entry[key][name] = max(entry[key].get(name, 0), duration)
        for key in (*self.tallies, *self.peaks):
            entry[key] = dict(
                sorted(entry[key].items(), key=lambda item: item[1], reverse=True)[: self.top_limit]
            )
        return entry

    def get_site_path(self, site: str) -> Path | None:
        """The site's file, or None for a name that is not a plain file name."""
        if not site or site.startswith(".") or any(character in site for character in "/\\\0"):
            return None
        return self.directory / f"{site}.json"

    @staticmethod
    def read_site(path: Path) -> dict:
        """The saved hours of a site, or none."""
        try:
            hours = json.loads(path.read_text())
        except (FileNotFoundError, ValueError):
            return {}
        return hours if isinstance(hours, dict) else {}

    def get_activity(self, site: str, now: datetime | None = None) -> SiteActivity:
        """The site's last 24 hours."""
        now = now or datetime.now(UTC)
        path = self.get_site_path(site)
        saved = self.read_site(path) if path else {}
        keys = self.get_hour_keys(now)
        hours = [saved.get(key) or self.new_hour() for key in keys]
        return SiteActivity(
            hours=[
                int(datetime.strptime(key, HOUR_FORMAT).replace(tzinfo=UTC).timestamp() * 1000)
                for key in keys
            ],
            requests=[hour["requests"] for hour in hours],
            errors=[hour["errors"] for hour in hours],
            jobs=[hour["jobs"] for hour in hours],
            response_ms=[
                round(hour["duration"] / hour["requests"] / 1000, 1) if hour["requests"] else 0
                for hour in hours
            ],
            top_paths=self.get_top(hours, "paths", sum),
            failed_paths=self.get_top(hours, "failed", sum),
            slow_requests=self.get_top(hours, "slow_paths", max, seconds=True),
            slow_jobs=self.get_top(hours, "slow_jobs", max, seconds=True),
        )

    @staticmethod
    def get_top(
        hours: list[dict],
        key: str,
        combine: Callable[[list[float]], float],
        seconds: bool = False,
        limit: int = 10,
    ) -> list[NamedValue]:
        """The top names of one list across the hours."""
        values: dict[str, list[float]] = {}
        for hour in hours:
            for name, value in hour[key].items():
                values.setdefault(name, []).append(value)
        totals = sorted(((name, combine(found)) for name, found in values.items()), key=lambda item: -item[1])
        return [
            NamedValue(name=name, value=round(value / 1_000_000, 3) if seconds else value)
            for name, value in totals[:limit]
        ]
