from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config import MariaDBConfig
from pilot.core.bench.directory import BenchDirectory
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class RemoteDatabaseSetup:
    """Move every bench on a host from Pilot's local MariaDB to a packaged MariaDB on another host.

    The local server and its data are deleted, so the host must not have sites yet.
    """

    def __init__(
        self,
        bench: Bench,
        host: str,
        ssh_user: str,
        ssh_key_path: str,
        port: int = 3306,
        admin_user: str = "pilot",
    ) -> None:
        self.bench = bench
        self.host = host.strip()
        self.ssh_user = ssh_user.strip()
        self.ssh_key_path = str(Path(ssh_key_path).expanduser())
        self.port = port
        self.admin_user = admin_user.strip()

    def run(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        """Take over the remote server, point every bench at it, then remove the local server."""
        from pilot.managers.database import MariaDBManager

        local = self.bench.config.mariadb
        remote = self.get_config()
        benches = self.get_benches()
        self.check(remote, benches)

        on_progress(f"Taking over MariaDB on {self.host}")
        MariaDBManager.for_config(remote).provision()
        on_progress("Saving the remote server for every bench on this host")
        self.save(remote, benches)
        for bench in benches:
            on_progress(f"Pointing {bench.config.name} at the remote server")
            bench.write_common_site_config()
            # The web unit of a production bench must stop depending on the local server before it goes.
            bench.rebuild_process_set(on_progress)
        if not local.remote:
            on_progress("Removing the local MariaDB server")
            MariaDBManager(local).remove_local_server()

    def get_config(self) -> MariaDBConfig:
        """The remote server's settings, with this host's memory share and a new admin password."""
        return MariaDBConfig(
            host=self.host,
            port=self.port,
            admin_user=self.admin_user,
            root_password=secrets.token_urlsafe(24),
            remote=True,
            ssh_user=self.ssh_user,
            ssh_key_path=self.ssh_key_path,
            memory_share=self.bench.config.mariadb.memory_share,
        )

    def get_benches(self) -> list[Bench]:
        """This bench and every other bench in its benches directory. They share one database server."""
        from pilot.core.bench import Bench

        others = [Bench(path) for path, _ in BenchDirectory.siblings_of(self.bench.path).iter_configs()]
        return [self.bench, *others]

    def check(self, remote: MariaDBConfig, benches: list[Bench]) -> None:
        """Refuse before any change: a wrong engine, an incomplete server, a missing key or sites to lose."""
        if self.bench.config.db_type != "mariadb":
            raise BenchError("A remote database server is supported only for MariaDB benches.")
        if self.bench.config.mariadb.existing:
            raise BenchError("This host uses an existing MariaDB server that Pilot does not manage.")
        remote.validate()
        if not Path(self.ssh_key_path).is_file():
            raise BenchError(f"SSH key {self.ssh_key_path} does not exist.")
        if sites := sorted(
            f"{bench.config.name}/{site.config.name}" for bench in benches for site in bench.sites()
        ):
            raise BenchError(
                f"These sites keep their databases on the local server: {', '.join(sites)}. "
                "Set up a remote server before the first site."
            )

    def save(self, remote: MariaDBConfig, benches: list[Bench]) -> None:
        """Write the remote server to common_config.toml, which every bench on the host shares."""
        from pilot.config.common import CommonConfig

        with CommonConfig.open(self.bench.path.parent) as common:
            common.mariadb = replace(remote)
        for bench in benches:
            bench.config.mariadb = replace(remote)
