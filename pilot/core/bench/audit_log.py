"""Bench-wide append-only audit log, sharded by ISO week."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pilot.internal.jsonl_log import JsonlLog, utc_now


@dataclass
class AuditEntry:
    """Shape of one audit record. Every call site passes only the fields relevant to
    its event, so everything but `type`/`logged_at` is optional - this is a type
    contract for the TS generator, not a struct `append()` requires callers to build."""

    type: str
    logged_at: str
    event: str | None = None
    site: str | None = None
    app: str | None = None
    status: str | None = None
    ip: str | None = None
    actor: str | None = None
    actor_jti: str | None = None
    jti: str | None = None
    command: str | None = None
    task_id: str | None = None
    args: dict | None = None
    provider: str | None = None
    username: str | None = None
    fingerprint: str | None = None
    patch: str | None = None
    operation: str | None = None
    device: str | None = None
    via: str | None = None
    scope: str | None = None
    timestamp: str | None = None
    finished_at: str | None = None
    with_files: bool | None = None
    file: str | None = None
    name: str | None = None

# Extra fields merged into every audit entry. The admin backend registers a provider that
# reads the current request (IP + actor); the CLI/worker leave the empty default.
def _context_provider() -> dict:
    return {}


def set_audit_context_provider(provider: Callable[[], dict]) -> None:
    global _context_provider
    _context_provider = provider


def audit_context() -> dict:
    """Registered context fields, or empty if none/failing. Never raises."""
    try:
        return _context_provider() or {}
    except Exception:
        return {}


class AuditLog:
    def __init__(self, bench) -> None:
        self._log = JsonlLog(bench.logs_path, "audit")

    def append(self, entry_type: str, entry: dict) -> None:
        self._log.append({"type": entry_type, "logged_at": utc_now(), **entry})

    def entries(self, entry_type=None, site=None, status=None, jti=None, limit=None) -> list[dict]:
        """Return matching records newest first across weekly files."""
        matched = []
        for record in self._log.read_newest_first():
            if self._matches(record, entry_type, site, status, jti):
                matched.append(record)
                if limit is not None and len(matched) >= limit:
                    break
        return matched

    @staticmethod
    def _matches(record: dict, entry_type, site, status, jti=None) -> bool:
        return (
            (entry_type is None or record.get("type") == entry_type)
            and (site is None or record.get("site") == site)
            and (status is None or record.get("status") == status)
            and (jti is None or jti in (record.get("jti"), record.get("actor_jti")))
        )
