from __future__ import annotations

import json
import time
import typing
from collections.abc import Callable, Iterable
from pathlib import Path

from pilot.core.notification.events import record_alert

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

PENDING = "pending"
FIRING = "firing"
RESOLVED = "resolved"
STATUSES = frozenset({PENDING, FIRING, RESOLVED})


class AlertLog:
    """The alert state of one monitor, one entry per condition, kept in one JSON file.

    A condition is `pending` until it holds for the window, then `firing` until it stays clear for the
    window, then `resolved`. A pending condition that clears is dropped, as nothing was sent for it. The
    change to firing and the change to resolved are each recorded in the bench feed once and sent once,
    retried each tick until they land. A resolved entry leaves the file once it has landed."""

    def __init__(self, path: Path, window_seconds: int) -> None:
        self.path = path
        self.window_seconds = window_seconds
        self.entries = self.read_entries()

    def update(self, active: Iterable[str], clear: Iterable[str]) -> None:
        """Move each condition on by one round. A condition in neither set keeps its state."""
        now = time.time()
        for name in active:
            self.entries[name] = self.get_active_entry(self.entries.get(name), now)
        for name in clear:
            if name in self.entries:
                self.clear_entry(name, now)
        self.write_entries()

    def retain(self, names: Iterable[str]) -> None:
        """Drop, without a resolution, every condition not in `names`, such as a deleted site."""
        kept = set(names)
        self.entries = {name: entry for name, entry in self.entries.items() if name in kept}
        self.write_entries()

    def deliver(
        self, bench: "Bench", status: str, get_payload: Callable[[list[str]], dict], grouped: bool = False
    ) -> None:
        """Record and send each change to `status` that has not landed yet. Grouped changes share one payload."""
        for names in self.get_batches(self.get_waiting(status, "recorded"), grouped):
            if record_alert(bench, get_payload(names)):
                self.mark(names, "recorded")
        for names in self.get_batches(self.get_waiting(status, "sent"), grouped):
            if bench.notifier.send(get_payload(names)):
                self.mark(names, "sent")

    def get_active_entry(self, entry: dict | None, now: float) -> dict:
        """The entry for a condition that holds this round. A resolved one that breaks again starts over."""
        if entry is None or entry["status"] == RESOLVED:
            entry = {"status": PENDING, "since": now}
        entry.pop("clear_since", None)
        if entry["status"] == PENDING and now - entry["since"] >= self.window_seconds:
            entry.update(status=FIRING, sent=False, recorded=False)
        return entry

    def clear_entry(self, name: str, now: float) -> None:
        """Count one clear round for the condition."""
        entry = self.entries[name]
        if entry["status"] == PENDING:
            del self.entries[name]
            return
        if entry["status"] == RESOLVED:
            return
        clear_since = entry.setdefault("clear_since", now)
        if now - clear_since >= self.window_seconds:
            # A resolution only has to reach the sinks that heard the condition fire.
            entry.update(status=RESOLVED, sent=not entry["sent"], recorded=not entry["recorded"])

    def get_waiting(self, status: str, field: str) -> list[str]:
        """The conditions in `status` whose change has not been `field` yet."""
        return sorted(
            name for name, entry in self.entries.items() if entry["status"] == status and not entry[field]
        )

    def mark(self, names: list[str], field: str) -> None:
        """Note that the change for these conditions has landed."""
        for name in names:
            self.entries[name][field] = True
        self.write_entries()

    @staticmethod
    def get_batches(names: list[str], grouped: bool) -> list[list[str]]:
        """All names as one batch when grouped, else one batch per name."""
        if grouped:
            return [names] if names else []
        return [[name] for name in names]

    def read_entries(self) -> dict[str, dict]:
        """The stored entries. A corrupt file, or an entry in an older format, starts its window over."""
        try:
            entries = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return {}
        if not isinstance(entries, dict):
            return {}
        return {
            name: entry
            for name, entry in entries.items()
            if isinstance(entry, dict) and entry.get("status") in STATUSES
        }

    def write_entries(self) -> None:
        """Store the entries without the resolved ones that have landed; remove the file once none are left."""
        self.entries = {
            name: entry
            for name, entry in self.entries.items()
            if not (entry["status"] == RESOLVED and entry["sent"] and entry["recorded"])
        }
        if not self.entries:
            self.path.unlink(missing_ok=True)
            return
        self.path.write_text(json.dumps(self.entries))
