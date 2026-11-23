from __future__ import annotations

import re
from functools import cached_property
from pathlib import Path

from pilot.core.mariadb_memory import (
    MariaDBMemorySizing,
    calculate_mariadb_memory,
    calculate_mariadb_variable_limits,
)
from pilot.exceptions import DatabaseError
from pilot.managers.database.mariadb import MariaDBManager
from pilot.managers.ssh_host import SSHHost


class RemoteMariaDBManager(MariaDBManager):
    """A packaged MariaDB on another host that Pilot manages over SSH.

    The package keeps its `mariadb` service, data directory and socket. Pilot owns two option files in
    the package's conf.d, which load after the package's own, and the service's memory limits.
    """

    _UNIT_NAME = "mariadb.service"
    # The package's unit allows this long for crash recovery, so a restart may block as long.
    service_timeout = 15 * 60
    remote_config_dir = Path("/etc/mysql/mariadb.conf.d")
    remote_log_dir = Path("/var/log/mysql")
    # Pilot's option file uses options that older servers refuse to start with.
    minimum_version = (11, 8)

    @cached_property
    def ssh_host(self) -> SSHHost:
        """The host the server runs on."""
        return SSHHost(self.config.host, self.config.ssh_user, self.config.ssh_key_path)

    @property
    def state_dir(self) -> Path:
        """Local state only: the action lock that serializes this host's changes to the remote server."""
        from pilot.core.server import Server

        return Server().root / "databases" / "mariadb-remote"

    @property
    def my_cnf_path(self) -> Path:
        """Pilot's option file on the remote host."""
        return self.remote_config_dir / "90-pilot.cnf"

    @property
    def managed_cnf_path(self) -> Path:
        """The variable editor's option file on the remote host. It loads last, so it wins."""
        return self.remote_config_dir / "99-pilot-managed.cnf"

    @property
    def error_log_path(self) -> Path:
        """The error log on the remote host. Deadlocks are read from here."""
        return self.remote_log_dir / "error.log"

    @property
    def slow_log_path(self) -> Path:
        """The slow query log on the remote host."""
        return self.remote_log_dir / "mariadb-slow.log"

    def is_installed(self) -> bool:
        """Whether the remote host has the MariaDB server."""
        return self.ssh_host.exists("/usr/sbin/mariadbd")

    def is_provisioned(self) -> bool:
        """Whether Pilot has written its options on the remote host."""
        return self.ssh_host.exists(str(self.my_cnf_path))

    def provision(self) -> None:
        """Take over the remote server: write Pilot's options, size it for its host and create the admin account."""
        if not self.config.root_password:
            raise DatabaseError("Set mariadb.root_password before Pilot takes over the remote server.")
        if not self.ssh_host.is_reachable():
            raise DatabaseError(
                f"Pilot cannot log in to {self.config.host} as {self.config.ssh_user} with passwordless sudo."
            )
        if not self.is_installed():
            raise DatabaseError(f"MariaDB is not installed on {self.config.host}.")
        if (version := self.get_server_version()) < self.minimum_version:
            raise DatabaseError(
                f"Pilot needs MariaDB {'.'.join(map(str, self.minimum_version))} or newer on "
                f"{self.config.host}, which runs {'.'.join(map(str, version))}."
            )

        with self.database_action_lock():
            sizing = self._write_config()
            self._install_unit(sizing)
            self._reset_failed_state()
            self.restart()
            self.secure_installation()
            self._wait_until_healthy()

    def get_server_version(self) -> tuple[int, int]:
        """The major and minor version of the remote server binary."""
        output = self.ssh_host.run(["/usr/sbin/mariadbd", "--version"])
        if not (match := re.search(r"Ver (\d+)\.(\d+)", output)):
            raise DatabaseError(f"Could not read the MariaDB version on {self.config.host}.")
        return int(match.group(1)), int(match.group(2))

    def _write_config(self) -> MariaDBMemorySizing:
        """Write Pilot's options into the remote conf.d, sized for the remote host's memory."""
        total_memory_mb = self._total_memory_mb()
        sizing = calculate_mariadb_memory(total_memory_mb, self.config.memory_share)
        limits = calculate_mariadb_variable_limits(total_memory_mb, self.config.memory_share)
        self._ensure_managed_cnf()
        content = (
            "# Managed by Pilot.\n"
            f"# pilot-sized-for-memory-mb = {total_memory_mb}\n"
            "[mysqld]\n"
            f"port = {self.config.port}\n"
            # Pilot connects from another host. The firewall and the account hosts limit who can.
            "bind-address = ::\n"
            # Accounts name Pilot's address, so a reverse lookup could only slow down or break a login.
            "skip-name-resolve = ON\n"
            f"log-error = {self.error_log_path}\n"
            "slow-query-log = ON\n"
            f"slow-query-log-file = {self.slow_log_path}\n"
            "long-query-time = 1\n"
            "log-output = FILE\n"
            "\n"
            f"{self._render_tuning_options(sizing, limits)}"
        )
        self.ssh_host.write_text(str(self.my_cnf_path), content)
        return sizing

    def ensure_managed_config(self) -> None:
        """Create the variable editor's option file. conf.d loads it without an include line."""
        self._ensure_managed_cnf()

    def has_managed_config(self) -> bool:
        """Whether the variable editor's option file exists on the remote host."""
        return self.ssh_host.exists(str(self.managed_cnf_path))

    def read_managed_config(self) -> str:
        """The text of the variable editor's option file on the remote host."""
        return self.ssh_host.read_text(str(self.managed_cnf_path))

    def write_managed_config(self, content: str) -> None:
        """Replace the variable editor's option file on the remote host."""
        self.ssh_host.write_text(str(self.managed_cnf_path), content)

    def _total_memory_mb(self) -> int:
        """The remote host's memory in MiB."""
        return self.ssh_host.total_memory_mb

    def _unit_memory_mb(self, name: str) -> int | None:
        """A memory property of the remote service in MiB, or None when unset."""
        value = self.ssh_host.run(["systemctl", "show", self._UNIT_NAME, f"--property={name}", "--value"])
        value = value.strip()
        # systemd prints "[not set]" for an unknown value and "infinity" for no limit.
        return int(value) // self.mebibyte if value.isdigit() else None

    def _set_runtime_memory_limits(self, sizing: MariaDBMemorySizing) -> None:
        """Set the service's memory limits until its next restart."""
        self._set_memory_limits(sizing, "--runtime")

    def _install_unit(
        self, sizing: MariaDBMemorySizing, *, preserve_swap_limit: bool = False, reload_manager: bool = True
    ) -> None:
        """Keep the service's memory limits across restarts. The package owns the unit itself."""
        self._set_memory_limits(sizing)

    def _set_memory_limits(self, sizing: MariaDBMemorySizing, *flags: str) -> None:
        """Set the service's memory limits with systemctl on the remote host."""
        self.ssh_host.run(
            [
                "systemctl",
                "set-property",
                *flags,
                self._UNIT_NAME,
                f"MemoryHigh={sizing.memory_high_mb}M",
                f"MemoryMax={sizing.memory_max_mb}M",
                "MemorySwapMax=0",
            ]
        )

    def is_running(self) -> bool:
        """Whether the remote service runs."""
        script = 'systemctl is-active --quiet "$1" && echo yes || echo no'
        return self.ssh_host.run(["sh", "-c", script, "sh", self._UNIT_NAME]).strip() == "yes"

    def is_reachable(self) -> bool:
        """Whether Pilot can log in to the remote server and run a query."""
        return self.is_healthy()

    def _control(self, action: str) -> None:
        """Run a service action on the remote host."""
        self.ssh_host.run(["systemctl", action, self._UNIT_NAME], timeout=self.service_timeout)

    def _reset_failed_state(self) -> None:
        """Clear a failed state of the remote service before it starts again."""
        self.ssh_host.run(["sh", "-c", 'systemctl reset-failed "$1" || true', "sh", self._UNIT_NAME])

    def _require_linux_managed_server(self) -> None:
        """Raise unless Pilot manages the remote server. Its host, not this one, must run Linux."""
        self._require_managed_server()

    def _run_sql_as_superuser(self, sql: str) -> None:
        """Run SQL as the remote host's root, which the package lets log in over the socket."""
        self.ssh_host.run(["mariadb"], stdin_text=sql)

    def get_admin_host(self) -> str:
        """Pilot's address as the remote host sees it, so the admin account accepts only this host."""
        return self.ssh_host.get_client_address()

    def get_account_host(self) -> str:
        """The host the admin connection comes from, so Pilot's accounts are no wider than the admin."""
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT SUBSTRING_INDEX(USER(), '@', -1)")
                row = cursor.fetchone()
        finally:
            connection.close()
        if not row or not row[0]:
            raise DatabaseError("MariaDB did not report the host of Pilot's connection.")
        return str(row[0])
