from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.core.database import make_database, site_database_name
from pilot.core.database.base import StorageComponent
from pilot.exceptions import CommandError
from pilot.utils import directory_bytes

_SYSTEM_SCHEMAS = {"mysql", "performance_schema", "information_schema", "sys"}

_ENGINES_WITH_SCHEMA_SIZES = {"mariadb", "postgres"}


@dataclass
class DatabaseRow:
    schema: str
    site: str | None
    system: bool
    bytes: int


@dataclass
class DatabaseBreakdown:
    """`core_bytes` is whatever the data directory holds beyond the databases
    and `components`. It is None when that directory cannot be measured from
    here - a remote server, or PostgreSQL's mode-0700 data directory."""

    engine: str
    supported: bool
    used_bytes: int
    binlog_bytes: int
    core_bytes: int | None = None
    components: list[StorageComponent] = field(default_factory=list)
    databases: list[DatabaseRow] = field(default_factory=list)


@dataclass
class StorageItem:
    name: str
    bytes: int


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


@dataclass
class BenchBreakdown:
    used_bytes: int
    apps: list[StorageItem]
    apps_bytes: int
    sites: list[SiteStorage]
    sites_bytes: int
    logs_bytes: int


@dataclass
class StorageBreakdown:
    disk_total: int
    disk_used: int
    database: DatabaseBreakdown
    bench: BenchBreakdown


@lru_cache(maxsize=256)
def directory_size_bytes(path: str) -> int:
    """Cached for the 10s /metrics poll; see `get_breakdown` for when it is
    cleared."""
    return directory_bytes(path)


def _data_directory_bytes(path: str | None) -> int | None:
    """None means "cannot be measured", which is not the same as empty: the
    server may be remote, and PostgreSQL keeps its data directory at 0700."""
    if not path:
        return None
    try:
        return directory_size_bytes(path)
    except (OSError, CommandError, IndexError, ValueError):
        return None


_PRIVATE_SUBDIRS_EXCLUDED_FROM_OTHER = {"files", "backups"}


def _storage_entry(path: Path) -> StorageItem:
    size = path.stat().st_size if path.is_file() else directory_size_bytes(str(path))
    return StorageItem(name=path.name, bytes=size)


def _site_backup_files(site_path: Path) -> list[StorageItem]:
    backups_dir = site_path / "private" / "backups"
    if not backups_dir.is_dir():
        return []
    return [_storage_entry(child) for child in backups_dir.iterdir()]


def _site_other_entries(site_path: Path) -> list[StorageItem]:
    entries: list[StorageItem] = []
    for child in site_path.iterdir():
        if child.name == "public":
            continue
        if child.name == "private":
            entries.extend(
                _storage_entry(grandchild)
                for grandchild in child.iterdir()
                if grandchild.name not in _PRIVATE_SUBDIRS_EXCLUDED_FROM_OTHER
            )
            continue
        entries.append(_storage_entry(child))
    return entries


class StorageProvider:
    """Disk usage breakdown for a bench's database engine and bench directories."""

    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root
        self._config = BenchConfig.read(bench_root, validate=False)
        self._bench = Bench(self._config, bench_root)

    def get_breakdown(self, disk_total: int, disk_used: int) -> StorageBreakdown:
        """Measures fresh. The cache exists for the 10s /metrics poll, and it
        never expires, so leaving it in place pins every directory to its first
        reading for the life of the process - including on an explicit refresh."""
        directory_size_bytes.cache_clear()
        return StorageBreakdown(
            disk_total=disk_total,
            disk_used=disk_used,
            database=self._database_breakdown(),
            bench=self._bench_breakdown(),
        )

    def _database_breakdown(self) -> DatabaseBreakdown:
        engine = self._config.db_type
        if engine not in _ENGINES_WITH_SCHEMA_SIZES:
            return DatabaseBreakdown(engine=engine, supported=False, used_bytes=0, binlog_bytes=0)

        database = make_database(self._config)
        databases = self._schema_sizes(database)
        components = database.get_storage_components()
        total_bytes = _data_directory_bytes(database.get_data_directory())
        accounted_bytes = sum(row.bytes for row in databases) + sum(
            component.bytes for component in components
        )
        binlog = next((c for c in components if c.key == "binlog"), None)
        return DatabaseBreakdown(
            engine=engine,
            supported=True,
            used_bytes=total_bytes if total_bytes is not None else accounted_bytes,
            binlog_bytes=binlog.bytes if binlog else 0,
            core_bytes=None if total_bytes is None else max(total_bytes - accounted_bytes, 0),
            components=components,
            databases=databases,
        )

    def _schema_sizes(self, database) -> list[DatabaseRow]:
        site_by_db = self._site_by_database_name()
        return [
            DatabaseRow(
                schema=schema,
                site=site_by_db.get(schema),
                system=schema in _SYSTEM_SCHEMAS,
                bytes=size,
            )
            for schema, size in database.get_schema_sizes().items()
        ]

    def _site_by_database_name(self) -> dict[str, str]:
        mapping = {}
        for site in self._bench.sites():
            try:
                db_name = site_database_name(self._bench_root, site.config.name)
            except (FileNotFoundError, ValueError):
                continue
            if db_name:
                mapping[db_name] = site.config.name
        return mapping

    def _bench_breakdown(self) -> BenchBreakdown:
        apps = [
            StorageItem(name=app.config.name, bytes=directory_size_bytes(str(app.path)))
            for app in self._bench.apps()
        ]
        sites = [self._site_storage(site) for site in self._bench.sites()]
        logs_bytes = directory_size_bytes(str(self._bench.logs_path))
        apps_bytes = sum(item.bytes for item in apps)
        sites_bytes = sum(item.bytes for item in sites)
        return BenchBreakdown(
            used_bytes=apps_bytes + sites_bytes + logs_bytes,
            apps=apps,
            apps_bytes=apps_bytes,
            sites=sites,
            sites_bytes=sites_bytes,
            logs_bytes=logs_bytes,
        )

    def _site_storage(self, site) -> SiteStorage:
        total_bytes = directory_size_bytes(str(site.path))
        private_files_bytes = directory_size_bytes(str(site.path / "private" / "files"))
        public_files_bytes = directory_size_bytes(str(site.path / "public"))
        backups_bytes = directory_size_bytes(str(site.path / "private" / "backups"))
        other_bytes = max(
            total_bytes - private_files_bytes - public_files_bytes - backups_bytes, 0
        )
        return SiteStorage(
            name=site.config.name,
            bytes=total_bytes,
            private_files_bytes=private_files_bytes,
            public_files_bytes=public_files_bytes,
            backups_bytes=backups_bytes,
            other_bytes=other_bytes,
            backup_files=_site_backup_files(site.path),
            other_entries=_site_other_entries(site.path),
        )
