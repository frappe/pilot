from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.core.bench.storage.models import BenchBreakdown, SiteStorage
from pilot.core.database import make_database, site_database_name
from pilot.core.database.base import StorageComponent
from pilot.exceptions import TaskConflictError, TaskNotFoundError
from pilot.internal.tasks.store import TaskStore
from pilot.tasks.refresh_bench_storage import RefreshBenchStorageTask


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
class BenchTask:
    """The refresh task measuring the bench folder now."""

    id: str
    status: str


@dataclass
class StorageBreakdown:
    """`bench` is the last report of the bench folder, None before the first one."""

    disk_total: int
    disk_used: int
    database: DatabaseBreakdown
    bench: BenchBreakdown | None
    bench_task: BenchTask | None


class StorageProvider:
    """Disk usage breakdown for a bench's database engine and bench directories."""

    system_schemas = frozenset({"mysql", "performance_schema", "information_schema", "sys"})
    engines_with_schema_sizes = frozenset({"mariadb", "postgres"})

    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root
        self._config = BenchConfig.read(bench_root, validate=False)
        self._bench = Bench(self._config, bench_root)

    def get_breakdown(
        self, disk_total: int, disk_used: int, refresh_if_stale: bool = False
    ) -> StorageBreakdown:
        """The database from a few queries, and the bench folder from its last report: a
        request never walks the disk. `refresh_if_stale` queues a measure when the report
        is missing or old and none runs, so a page that polls with it retries a failed one."""
        bench = self._bench.storage.read()
        task_id = self.get_bench_task_id()
        if refresh_if_stale and task_id is None and (bench is None or self._bench.storage.is_stale(bench)):
            task_id = self.refresh_bench()
        return StorageBreakdown(
            disk_total=disk_total,
            disk_used=disk_used,
            database=self._database_breakdown(bench.database_directory_bytes if bench else None),
            bench=bench,
            bench_task=self.get_bench_task(task_id),
        )

    def get_bench_task(self, task_id: str | None) -> BenchTask | None:
        """The task with its status, or None when it is gone."""
        if task_id is None:
            return None
        try:
            return BenchTask(id=task_id, status=TaskStore(self._bench_root).read_status(task_id).value)
        except TaskNotFoundError:
            return None

    def get_bench_task_id(self) -> str | None:
        """The refresh task measuring the bench folder now, if any."""
        return TaskStore(self._bench_root).get_active_task_id(RefreshBenchStorageTask.static_resource_keys[0])

    def refresh_bench(self) -> str:
        """Queue a measure of the bench folder, or return the one already running. A
        conflict whose task ended before the lookup queues once more."""
        for _ in range(2):
            try:
                return RefreshBenchStorageTask.queue(self._bench)
            except TaskConflictError:
                task_id = self.get_bench_task_id()
                if task_id is not None:
                    return task_id
        raise TaskConflictError("Could not queue a bench storage measure")

    def get_site(self, name: str) -> SiteStorage:
        """One site's folder breakdown, measured now."""
        return self._bench.storage.get_site(self._bench.site(name))

    def _database_breakdown(self, directory_bytes: int | None) -> DatabaseBreakdown:
        """The database storage breakdown. `directory_bytes` is the data directory from the
        last bench report; None leaves `core_bytes` unknown."""
        engine = self._config.db_type
        if engine not in self.engines_with_schema_sizes:
            return DatabaseBreakdown(engine=engine, supported=False, used_bytes=0, binlog_bytes=0)

        database = make_database(self._config)
        databases = self._schema_sizes(database)
        components = database.get_storage_components()
        total_bytes = directory_bytes
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
        """The size of each schema with its site."""
        site_by_db = self._site_by_database_name()
        return [
            DatabaseRow(
                schema=schema,
                site=site_by_db.get(schema),
                system=schema in self.system_schemas,
                bytes=size,
            )
            for schema, size in database.get_schema_sizes().items()
        ]

    def _site_by_database_name(self) -> dict[str, str]:
        """The site name of each database."""
        mapping = {}
        for site in self._bench.sites():
            try:
                db_name = site_database_name(self._bench_root, site.config.name)
            except (FileNotFoundError, ValueError):
                continue
            if db_name:
                mapping[db_name] = site.config.name
        return mapping
