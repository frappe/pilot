from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StorageItem:
    name: str
    bytes: int


@dataclass
class AppStorage:
    """`bytes` covers the whole app folder, including its git history and node_modules."""

    name: str
    bytes: int
    git_bytes: int
    node_modules_bytes: int


@dataclass
class SiteStorage:
    name: str
    bytes: int
    private_files_bytes: int
    public_files_bytes: int
    backups_bytes: int
    other_bytes: int
    backup_files: list[StorageItem] = field(default_factory=list)
    other_entries: list[StorageItem] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict) -> SiteStorage:
        """The site row a report file holds."""
        return cls(
            **{
                **payload,
                "backup_files": [StorageItem(**item) for item in payload["backup_files"]],
                "other_entries": [StorageItem(**item) for item in payload["other_entries"]],
            }
        )


@dataclass
class BenchBreakdown:
    """`used_bytes` is the whole bench folder, so it also holds the folder entries no part names.
    `database_directory_bytes` is the database server's data directory, measured by the same task
    so that a request never walks it; None when it cannot be measured from here."""

    used_bytes: int
    apps: list[AppStorage]
    apps_bytes: int
    env_bytes: int
    sites: list[SiteStorage]
    sites_bytes: int
    logs_bytes: int
    others: list[StorageItem]
    others_bytes: int
    database_directory_bytes: int | None
    measured_at: str

    @classmethod
    def from_dict(cls, payload: dict) -> BenchBreakdown:
        """The breakdown a report file holds."""
        return cls(
            **{
                **payload,
                "apps": [AppStorage(**app) for app in payload["apps"]],
                "sites": [SiteStorage.from_dict(site) for site in payload["sites"]],
                "others": [StorageItem(**item) for item in payload["others"]],
            }
        )
