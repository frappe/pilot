from __future__ import annotations

from typing import TypedDict

from pilot.config.backup import BackupConfig


class BackupFile(TypedDict):
    filename: str
    path: str
    size_bytes: int
    kind: str


class Backup(TypedDict):
    timestamp: str
    created_at: str
    is_offsite: bool
    files: list[BackupFile]


class BackupSchedule(TypedDict):
    schedule: str | None
    retention: BackupConfig | None
