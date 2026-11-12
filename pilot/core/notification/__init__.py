"""Bench-local notification feed.

The feed is the bench's own record of what went wrong - failed tasks, sustained
resource alerts, sites that stopped answering. It is written and read entirely on
this machine, so a bench that never enrolls with Central still has one. Forwarding
an event onward is a separate concern that lives in `pilot.core.notification.notifier`.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
from pilot.internal.jsonl_log import JsonlLog, utc_now

CATEGORIES = ("Sites", "Tasks", "Server", "Updates")
SEVERITIES = ("Info", "Warning", "Error")

# A badge stops being useful long before this, and an unread scan on a bench that
# never marks anything read should not walk a year of shards.
UNREAD_SCAN_LIMIT = 200


@dataclass
class Notification:
    """One entry in the feed. `is_read` is derived from the read-state file rather
    than stored on the record, so marking one read never rewrites the log."""

    name: str
    category: str
    event: str
    severity: str
    title: str
    created_at: str
    is_read: bool
    message: str | None = None
    site: str | None = None
    task_id: str | None = None
    action_route: str | None = None


@dataclass(frozen=True)
class ReadMarks:
    """A snapshot of the read-state file, taken once per feed read so answering
    `is_read` for a whole page costs one file read rather than one per row."""

    read_through: str
    names: frozenset[str]

    def is_read(self, name: str, created_at: str) -> bool:
        """Whether the notification is read."""
        return created_at <= self.read_through or name in self.names


class ReadState:
    """Which notifications have been seen. `read_through` is what "mark all read"
    writes: everything created at or before it is read, so the explicit name list
    only ever holds the handful read individually since then."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> ReadMarks:
        """A hand-edited or truncated file reads as "nothing read yet" rather than
        breaking the feed."""
        try:
            state = json.loads(self.path.read_text())
            return ReadMarks(str(state.get("read_through") or ""), frozenset(state.get("names") or ()))
        except (FileNotFoundError, ValueError, AttributeError):
            return ReadMarks("", frozenset())

    def add(self, name: str) -> None:
        """Read-modify-write under the file's lock. Two admin requests marking
        different notifications read at once would otherwise each write back the
        snapshot they started from, and the later write would drop the other's mark."""
        with self.lock():
            marks = self.read()

            if name in marks.names:
                return

            self._write_locked(marks.read_through, marks.names | {name})

    def mark_through(self) -> None:
        """The watermark is taken under the lock, not before it. Taken before, a
        notification created after it could be marked read by another request while
        this one waited - and clearing the name set would drop that mark for a
        notification the watermark does not reach."""
        with self.lock():
            self._write_locked(utc_now(), frozenset())

    @contextmanager
    def lock(self) -> Iterator[None]:
        """The feed's write lock. Read state takes it, and so does `create` while it
        stamps and appends: a record stamped before a watermark but appended after it
        would be born read and never appear at all.

        Nothing called while it is held may take it again - that would block on itself.

        Read state can be written before anything has ever been logged, so the
        directory the lock file lives in is not guaranteed to exist yet."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path):
            yield

    def _write_locked(self, read_through: str, names: frozenset[str]) -> None:
        """Save the read state; the caller holds the lock."""
        payload = {"read_through": read_through, "names": sorted(names)}
        replace_private_text_locked(self.path, json.dumps(payload))


class NotificationStore:
    """The feed for one bench. Reach it as `bench.notifications`."""

    def __init__(self, logs_path: Path) -> None:
        self._log = JsonlLog(Path(logs_path), "notifications")
        self.read_state = ReadState(Path(logs_path) / "notifications_read.json")

    def create(
        self,
        title: str,
        *,
        category: str,
        event: str,
        severity: str = "Info",
        message: str | None = None,
        site: str | None = None,
        task_id: str | None = None,
        action_route: str | None = None,
    ) -> Notification:
        """Add a notification and return it; raise for an unknown category or severity."""
        if category not in CATEGORIES:
            raise ValueError(f"Unknown notification category: {category!r}")
        if severity not in SEVERITIES:
            raise ValueError(f"Unknown notification severity: {severity!r}")

        # Stamped and appended under the feed lock, so "mark all read" either covers
        # this record or writes a watermark that does not reach it. Stamping outside
        # the lock leaves a window where it is born read and never shows up at all.
        with self.read_state.lock():
            notification = Notification(
                name=self._next_name(),
                category=category,
                event=event,
                severity=severity,
                title=title,
                created_at=utc_now(),
                is_read=False,
                message=message,
                site=site,
                task_id=task_id,
                action_route=action_route,
            )
            record = asdict(notification)
            record.pop("is_read")
            self._log.append(record)

        return notification

    def list(self, limit: int, *, category: str | None = None, unread_only: bool = False) -> list[Notification]:
        """Newest first. `limit` is exact - filtering happens while scanning, so a
        page is never short of rows that exist further back in the log."""
        marks = self.read_state.read()
        matched: list[Notification] = []
        for record in self._log.read_newest_first():
            notification = self._build(record, marks)
            if notification is None:
                continue
            if category and notification.category != category:
                continue
            if unread_only and notification.is_read:
                continue
            matched.append(notification)
            if len(matched) >= limit:
                break
        return matched

    @property
    def unread_count(self) -> int:
        """Counts up to UNREAD_SCAN_LIMIT. The scan stops at the mark-all-read
        watermark, so on a bench that keeps its feed clear this reads a few lines."""
        marks = self.read_state.read()
        count = 0
        for record in self._log.read_newest_first():
            created_at = str(record.get("created_at") or "")
            if created_at and created_at <= marks.read_through:
                break
            if record.get("name") not in marks.names:
                count += 1
            if count >= UNREAD_SCAN_LIMIT:
                break
        return count

    def mark_read(self, name: str) -> None:
        """Mark one notification as read."""
        self.read_state.add(name)

    def mark_all_read(self) -> None:
        """Mark every notification as read."""
        self.read_state.mark_through()

    def _build(self, record: dict, marks: ReadMarks) -> Notification | None:
        """A record missing the fields that identify it is skipped - a partial line
        is not worth failing the whole feed over."""
        name = record.get("name")
        created_at = record.get("created_at")
        if not name or not created_at:
            return None
        return Notification(
            name=name,
            category=record.get("category") or "Server",
            event=record.get("event") or "",
            severity=record.get("severity") or "Info",
            title=record.get("title") or "",
            created_at=created_at,
            is_read=marks.is_read(name, created_at),
            message=record.get("message"),
            site=record.get("site"),
            task_id=record.get("task_id"),
            action_route=record.get("action_route"),
        )

    @staticmethod
    def _next_name() -> str:
        """Sortable and unique: millisecond stamp plus a random tail, so two
        notifications raised in the same millisecond still get distinct names."""
        return f"{int(time.time() * 1000):013d}-{secrets.token_hex(4)}"
