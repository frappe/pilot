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


class RemoteBackup(TypedDict):
    timestamp: str
    created_at: str
    parts: list[str]


class RemoteBackupList(TypedDict):
    backups: list[RemoteBackup]


class BackupSchedule(TypedDict):
    schedule: str | None
    retention: BackupConfig | None


class FrappeCloudConnection(TypedDict):
    status: str
    remote_site: str
    approval_url: str
    code: str


class FrappeCloudBackup(TypedDict):
    name: str
    created_at: str
    size_bytes: int


class FrappeCloudBackupList(TypedDict):
    backups: list[FrappeCloudBackup]
    running_backup: str | None


class BackupUploadStarted(TypedDict):
    upload_id: str
    chunk_size: int


class BackupUploadFile(TypedDict):
    size: int
    received: int


class BackupUploadStatus(TypedDict):
    files: dict[str, BackupUploadFile]
