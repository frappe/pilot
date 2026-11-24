from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import cached_property
from typing import TYPE_CHECKING

from pilot.core.bench.remote_database.site_database import SiteDatabase, get_rows, get_site_label
from pilot.exceptions import BenchError, InsufficientDiskSpaceError

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.managers.database import MariaDBManager
    from pilot.managers.database.remote_mariadb import RemoteMariaDBManager
    from pilot.managers.processes.local import ProcessManager


class RemoteDatabaseMigration:
    """Copy the site databases of every bench on a host from Pilot's local MariaDB to the remote server.

    The benches' workloads stop for the copy. Nothing on the local server and no bench config changes until every
    copy is verified, and a failure drops only what this run created on the remote server.
    """

    # The local server drops a dump that waits this long for the import to read it. The default is 60 s.
    dump_write_timeout = 3600
    # Table statistics only estimate the size, and the import also writes redo and temporary files.
    disk_margin = 1.5
    disk_reserve_bytes = 1024**3

    def __init__(
        self,
        local: MariaDBManager,
        remote: RemoteMariaDBManager,
        benches: list[Bench],
        on_progress: Callable[[str], None],
    ) -> None:
        self.local = local
        self.remote = remote
        self.benches = benches
        self.on_progress = on_progress

    @cached_property
    def databases(self) -> list[SiteDatabase]:
        """The database of every site on the host that keeps it on MariaDB."""
        databases = []
        for bench in self.benches:
            for site in bench.sites():
                if database := SiteDatabase.for_site(site, self.local):
                    databases.append(database)
                else:
                    self.on_progress(f"Skipping {get_site_label(site)}: its database is not on MariaDB")
        return databases

    @property
    def schema_list(self) -> str:
        """The site database names as an SQL list."""
        return ", ".join(self.local._sql_quote(database.name) for database in self.databases)

    def check(self) -> None:
        """Refuse before any change: running work, a local server that does not answer, or a remote server that
        cannot take the copy."""
        self.check_running_work()
        if not self.local.has_valid_credentials():
            raise BenchError(
                "Pilot cannot log in to its local MariaDB server. Start it, then run the move again."
            )
        if not self.databases:
            return
        self.check_version()
        self.check_definers()
        self.check_disk()

    def check_running_work(self) -> None:
        """Refuse while `pilot start` or a task may write to a site database during the copy."""
        from pilot.managers.processes.local import ProcessManager
        from pilot.managers.task import TaskActivityReader

        for bench in self.benches:
            name = bench.config.name
            if not bench.config.production.enabled and ProcessManager(bench).is_running():
                raise BenchError(f"`pilot start` runs bench {name}. Stop it, then run the move again.")
            activity = TaskActivityReader(bench.path).read()
            if activity.queued_tasks or activity.running_tasks:
                raise BenchError(
                    f"Bench {name} has queued or running tasks. Run the move again after they end."
                )

    def check_version(self) -> None:
        """Refuse a remote server older than the local one: a newer server's dump may not load into it."""
        version = get_rows(self.local, "SELECT VERSION();")[0]
        local = tuple(int(part) for part in version.split("-")[0].split(".")[:2])
        remote = tuple(self.remote.get_health()["version"])
        if remote < local:
            raise BenchError(
                f"The remote server runs MariaDB {'.'.join(map(str, remote))}, which is older than the local "
                f"server's {'.'.join(map(str, local))}. Use a database VM with MariaDB {'.'.join(map(str, local))} "
                "or newer."
            )

    def check_definers(self) -> None:
        """Refuse objects that run as their definer. The definer is an account of the local server, which the remote
        server does not have, so they would fail there."""
        schemas = self.schema_list
        rows = get_rows(
            self.local,
            f"SELECT CONCAT(TABLE_SCHEMA, ' view ', TABLE_NAME) FROM information_schema.VIEWS "
            f"WHERE TABLE_SCHEMA IN ({schemas}) AND SECURITY_TYPE = 'DEFINER'\n"
            f"UNION ALL SELECT CONCAT(ROUTINE_SCHEMA, ' ', LOWER(ROUTINE_TYPE), ' ', ROUTINE_NAME) "
            f"FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA IN ({schemas}) AND SECURITY_TYPE = 'DEFINER'\n"
            f"UNION ALL SELECT CONCAT(TRIGGER_SCHEMA, ' trigger ', TRIGGER_NAME) FROM information_schema.TRIGGERS "
            f"WHERE TRIGGER_SCHEMA IN ({schemas})\n"
            f"UNION ALL SELECT CONCAT(EVENT_SCHEMA, ' event ', EVENT_NAME) FROM information_schema.EVENTS "
            f"WHERE EVENT_SCHEMA IN ({schemas});",
        )
        if rows:
            raise BenchError(
                f"These objects run as an account of the local server, which the remote server does not have: "
                f"{', '.join(rows)}. Drop them or make the views and routines SQL SECURITY INVOKER, then run the "
                "move again."
            )

    def check_disk(self) -> None:
        """Refuse when the database VM's disk cannot hold the site databases, measured as the data and index
        length that the local server reports for their tables."""
        size = int(
            get_rows(
                self.local,
                "SELECT COALESCE(SUM(DATA_LENGTH + INDEX_LENGTH), 0) FROM information_schema.TABLES "
                f"WHERE TABLE_SCHEMA IN ({self.schema_list});",
            )[0]
        )
        needed = int(size * self.disk_margin)
        free = self.remote.agent.get_free_disk_bytes()
        if free < needed + self.disk_reserve_bytes:
            raise InsufficientDiskSpaceError(
                "the site databases on the database VM", needed, free, self.disk_reserve_bytes
            )

    def check_remote_names(self) -> None:
        """Refuse when the remote server has a database or an account with a site database's name. A copy must
        never write into, or a cleanup drop, what was there before."""
        if not self.databases:
            return
        names = sorted(
            {database.name for database in self.databases} | {database.user for database in self.databases}
        )
        listed = ", ".join(self.remote._sql_quote(name) for name in names)
        rows = get_rows(
            self.remote,
            f"SELECT CONCAT('database ', SCHEMA_NAME) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME IN ({listed})\n"
            f"UNION ALL SELECT CONCAT('account ', User, '@', Host) FROM mysql.user WHERE User IN ({listed});",
        )
        if rows:
            raise BenchError(
                f"The remote server already has {', '.join(rows)}. Pilot copies a site's database only to a server "
                f"without one of that name. Drop them on {self.remote.config.host}, then run the move again."
            )

    def run(self, switch: Callable[[], None]) -> None:
        """With the workloads stopped, copy and verify every database, then `switch` the benches to the remote server."""
        self.check_remote_names()
        host = self.remote.get_account_host()
        with self.stopped_workloads():
            self.copy(host)
            switch()

    def copy(self, host: str) -> None:
        """Create, copy and verify each database and its login at `host`. On failure drop what this run created."""
        created: list[SiteDatabase] = []
        try:
            with self.longer_dump_timeout():
                for database in self.databases:
                    self.on_progress(f"Copying the database of {database.label}")
                    created.append(database)
                    database.create(self.remote, host)
                    database.copy(self.local, self.remote)
                    database.verify(self.local, self.remote)
        except BaseException as error:
            self.drop(created, host, error)
            raise

    def drop(self, created: list[SiteDatabase], host: str, error: BaseException) -> None:
        """Drop the copies this run created. A drop that fails is noted on `error`, which the caller re-raises."""
        for database in created:
            self.on_progress(f"Dropping the copy of {database.label} from the remote server")
            try:
                database.drop(self.remote, host)
            except Exception as failure:
                error.add_note(
                    f"Pilot could not drop the copy of {database.label} from the remote server: {failure}"
                )

    @contextmanager
    def longer_dump_timeout(self) -> Iterator[None]:
        """Let a dump wait for a slow import on the remote server, as a large table makes it."""
        previous = int(get_rows(self.local, "SELECT @@global.net_write_timeout;")[0])
        self.local.run_admin_sql(f"SET GLOBAL net_write_timeout = {self.dump_write_timeout};")
        try:
            yield
        finally:
            self.local.run_admin_sql(f"SET GLOBAL net_write_timeout = {previous};")

    @contextmanager
    def stopped_workloads(self) -> Iterator[None]:
        """Stop each running production workload, and start it again however the block ends."""
        from pilot.managers.processes.local import ProcessManager

        stopped: list[ProcessManager] = []
        try:
            for bench in self.benches:
                manager = ProcessManager.for_bench(bench)
                if bench.config.production.enabled and manager.is_running():
                    self.on_progress(f"Stopping bench {bench.config.name}")
                    stopped.append(manager)
                    manager.stop()
            yield
        except BaseException as error:
            self.start_workloads(stopped, error)
            raise
        self.start_workloads(stopped)

    def start_workloads(self, stopped: list[ProcessManager], error: BaseException | None = None) -> None:
        """Start each stopped workload that does not run. A failure is noted on `error`, or raised without one."""
        failures = []
        for manager in stopped:
            if manager.is_running():
                continue
            self.on_progress(f"Starting bench {manager.bench.config.name}")
            try:
                manager.start_workload()
            except Exception as failure:
                failures.append(f"{manager.bench.config.name}: {failure}")
        if not failures:
            return
        message = f"Pilot could not start these benches again: {'; '.join(failures)}"
        if error is None:
            raise BenchError(message)
        error.add_note(message)
