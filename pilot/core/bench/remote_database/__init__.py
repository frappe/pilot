from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from pilot.config import MariaDBConfig
from pilot.core.bench.directory import BenchDirectory
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.core.bench.remote_database.migration import RemoteDatabaseMigration
    from pilot.core.bench.remote_database.site_database import SiteDatabase


class RemoteDatabaseSetup:
    """Move every bench on a host from Pilot's local MariaDB to the MariaDB on a database VM.

    Without `migrate` the host must have no sites, and the local server and its data are deleted. With `migrate`
    the site databases are copied first, and the local server's data stays unless `delete_local_data` is set.
    """

    def __init__(
        self,
        bench: Bench,
        host: str,
        agent_token: str,
        port: int = 3306,
        agent_port: int = 7781,
        admin_user: str = "pilot",
        migrate: bool = False,
        delete_local_data: bool = False,
    ) -> None:
        self.bench = bench
        self.host = host.strip()
        self.agent_token = agent_token.strip()
        self.port = port
        self.agent_port = agent_port
        self.admin_user = admin_user.strip()
        self.migrate = migrate
        self.delete_local_data = delete_local_data

    def run(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        """Take over the remote server, copy the site databases with `migrate`, point every bench at the remote
        server, then remove the local server."""
        from pilot.managers.database import MariaDBManager

        local = self.bench.config.mariadb
        remote = self.get_config()
        benches = self.get_benches()
        self.check(remote, benches)
        migration = self.get_migration(local, remote, benches, on_progress) if self.migrate else None
        if migration:
            migration.check()

        on_progress(f"Taking over MariaDB on {self.host}")
        MariaDBManager.for_config(remote).provision()
        if migration:
            migration.run(lambda: self.switch(remote, benches, on_progress, migration.databases))
        else:
            self.switch(remote, benches, on_progress)
        self.remove_local_server(local, on_progress)

    def get_config(self) -> MariaDBConfig:
        """The remote server's settings, with a new admin password."""
        return MariaDBConfig(
            host=self.host,
            port=self.port,
            admin_user=self.admin_user,
            root_password=secrets.token_urlsafe(24),
            remote=True,
            agent_port=self.agent_port,
            agent_token=self.agent_token,
        )

    def get_benches(self) -> list[Bench]:
        """This bench and every other bench in its benches directory. They share one database server."""
        from pilot.core.bench import Bench

        others = [Bench(path) for path, _ in BenchDirectory.siblings_of(self.bench.path).iter_configs()]
        return [self.bench, *others]

    def get_migration(
        self,
        local: MariaDBConfig,
        remote: MariaDBConfig,
        benches: list[Bench],
        on_progress: Callable[[str], None],
    ) -> RemoteDatabaseMigration:
        """The copy of the site databases from the local server to the remote one."""
        from pilot.core.bench.remote_database.migration import RemoteDatabaseMigration
        from pilot.managers.database import MariaDBManager
        from pilot.managers.database.remote_mariadb import RemoteMariaDBManager

        return RemoteDatabaseMigration(
            MariaDBManager(local), RemoteMariaDBManager(remote), benches, on_progress
        )

    def check(self, remote: MariaDBConfig, benches: list[Bench]) -> None:
        """Refuse before any change: a wrong engine, an incomplete server, or sites to lose without `migrate`."""
        if self.bench.config.db_type != "mariadb":
            raise BenchError("A remote database server is supported only for MariaDB benches.")
        if self.bench.config.mariadb.existing:
            raise BenchError("This host uses an existing MariaDB server that Pilot does not manage.")
        if self.bench.config.mariadb.remote:
            raise BenchError(
                f"This host already uses the MariaDB server on {self.bench.config.mariadb.host}."
            )
        if self.delete_local_data and not self.migrate:
            raise BenchError("--delete-local-data applies only with --migrate.")
        remote.validate()
        if self.migrate:
            return
        if sites := sorted(
            f"{bench.config.name}/{site.config.name}" for bench in benches for site in bench.sites()
        ):
            raise BenchError(
                f"These sites keep their databases on the local server: {', '.join(sites)}. "
                "Run the command with --migrate to copy them to the remote server."
            )

    def switch(
        self,
        remote: MariaDBConfig,
        benches: list[Bench],
        on_progress: Callable[[str], None],
        databases: Sequence[SiteDatabase] = (),
    ) -> None:
        """Point every bench and every copied site at the remote server."""
        on_progress("Saving the remote server for every bench on this host")
        self.save(remote, benches)
        for database in databases:
            database.point_site_at(remote)
        for bench in benches:
            on_progress(f"Pointing {bench.config.name} at the remote server")
            bench.write_common_site_config()
            # The web unit of a production bench must stop depending on the local server before it goes.
            bench.rebuild_process_set(on_progress)

    def save(self, remote: MariaDBConfig, benches: list[Bench]) -> None:
        """Write the remote server to common_config.toml, which every bench on the host shares."""
        from pilot.config.common import CommonConfig

        with CommonConfig.open(self.bench.path.parent) as common:
            common.mariadb = replace(remote)
        for bench in benches:
            bench.config.mariadb = replace(remote)

    def remove_local_server(self, local: MariaDBConfig, on_progress: Callable[[str], None]) -> None:
        """Stop the local server and remove its units. A migration keeps its data unless told to delete it."""
        from pilot.managers.database import MariaDBManager

        manager = MariaDBManager(local)
        keep_data = self.migrate and not self.delete_local_data
        on_progress("Removing the local MariaDB server")
        manager.remove_local_server(keep_data=keep_data)
        if keep_data:
            on_progress(
                f"The local server's data stays in {manager.state_dir}. Delete it with `rm -rf {manager.state_dir}` "
                "once the sites work on the remote server."
            )
