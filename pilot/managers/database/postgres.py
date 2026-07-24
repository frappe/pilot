from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

from pilot.config import PostgresConfig
from pilot.exceptions import DatabaseError
from pilot.managers.database.base import UserOwnedDBManager
from pilot.managers.platform import is_macos, which
from pilot.utils import run_command

# One rootless PostgreSQL server per OS user, shared by all their benches.
_STATE_DIR = Path.home() / ".local" / "share" / "pilot" / "postgres"
_CLIENT_TIMEOUT = 5
_DEBIAN_POSTGRES_ROOT = Path("/usr/lib/postgresql")


class PostgresManager(UserOwnedDBManager):
    """Manages the shared per-user PostgreSQL server."""

    _UNIT_NAME = "pilot-postgres.service"
    _DISPLAY_NAME = "PostgreSQL"
    _SYSTEM_PACKAGE = "postgresql"
    _BREW_FORMULA_BASE = "postgresql"
    _DEFAULT_VERSION = "16"

    def __init__(self, config: PostgresConfig) -> None:
        self.config = config

    @property
    def data_dir(self) -> Path:
        return _STATE_DIR / "data"

    @property
    def socket_dir(self) -> Path:
        # Postgres' compiled-in default (often /var/run/postgresql) is owned by
        # the 'postgres' OS user/group, not the bench user - pin a directory
        # we actually own so both the server and psql can use it.
        return _STATE_DIR / "run"

    def is_installed(self) -> bool:
        return bool(which("psql") or which("postgres") or which("initdb"))

    def is_provisioned(self) -> bool:
        if is_macos():
            return self.is_running() and not self.is_unsecured()
        return super().is_provisioned()

    def is_unsecured(self) -> bool:
        """True when the admin role has no password in pg_authid."""
        role = self._sql_quote(self.config.admin_user)
        cmd = [
            self._psql() or "psql",
            "-p",
            str(self.config.port),
            "-d",
            "postgres",
            "-tAc",
            f"SELECT rolpassword IS NULL FROM pg_authid WHERE rolname = {role}",
        ]
        if not is_macos():
            cmd[1:1] = ["-h", str(self.socket_dir)]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_CLIENT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return False
        if result.returncode != 0:
            return False
        output = result.stdout.strip()
        # Empty output means the role doesn't exist yet at all - also fresh.
        return output in ("", "t")

    def provision(self) -> None:
        """Install, start and secure the shared PostgreSQL server."""
        self.install()
        if is_macos():
            if not self.is_running():
                self.start()
        else:
            self._provision_user_owned()
        self._wait_until_reachable()
        self.secure()

    def _provision_user_owned(self) -> None:
        if not self.is_provisioned():
            self._ensure_port_available()
            self.data_dir.parent.mkdir(parents=True, exist_ok=True)
            self.socket_dir.mkdir(parents=True, exist_ok=True)
            # No --username: the bootstrap superuser is the bench OS user.
            run_command([self._server_binary("initdb"), "-D", str(self.data_dir)])
            self._install_unit()
            run_command(self._systemctl("enable", "--now", self._UNIT_NAME), env=self._systemctl_env())
        elif not self.is_running():
            run_command(self._systemctl("start", self._UNIT_NAME), env=self._systemctl_env())

    def _ensure_port_available(self) -> None:
        """Fail if an unowned process is already listening on the configured port."""
        try:
            with socket.create_connection(("127.0.0.1", self.config.port), timeout=0.3):
                pass
        except OSError:
            return  # nothing listening there - free to bind
        raise DatabaseError(
            f"Port {self.config.port} is already in use by another service "
            f"(e.g. a system-wide PostgreSQL). Free it, or set postgres.port in "
            f"bench.toml to an unused port, then retry."
        )

    def _install_unit(self) -> None:
        postgres = self._server_binary("postgres")
        content = (
            "[Unit]\n"
            "Description=PostgreSQL (pilot, user-owned)\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart={postgres} -D {self.data_dir} -p {self.config.port} "
            f"-c listen_addresses=127.0.0.1 -c unix_socket_directories={self.socket_dir}\n"
            "Restart=on-failure\n\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )
        unit_dir = self._user_unit_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        self.unit_path.write_text(content)
        run_command(self._systemctl("daemon-reload"), env=self._systemctl_env())

    def secure(self) -> None:
        """Create/update the admin role so Frappe can connect over TCP."""
        if not self.config.root_password:
            print(
                "  postgres.root_password is empty - skipping superuser setup. "
                "Set it in Settings before creating PostgreSQL sites."
            )
            return
        if self.has_valid_credentials():
            return
        self._run_sql_as_superuser(self._ensure_role_sql())
        if not self.has_valid_credentials():
            raise DatabaseError(
                f"PostgreSQL is installed but bench could not authenticate as '{self.config.admin_user}' "
                "over TCP. Ensure the server's pg_hba.conf allows password auth from localhost, or set "
                "postgres.root_password to the existing superuser password."
            )

    def has_valid_credentials(self, password: str | None = None) -> bool:
        """Check admin credentials using PGPASSWORD, never argv."""
        pw = self.config.root_password if password is None else password
        psql = self._psql()
        if not psql:
            return False
        try:
            result = subprocess.run(
                [
                    psql,
                    "-h",
                    self.config.host,
                    "-p",
                    str(self.config.port),
                    "-U",
                    self.config.admin_user,
                    "-d",
                    "postgres",
                    "-tAc",
                    "SELECT 1",
                ],
                env={
                    **os.environ,
                    "PGPASSWORD": pw,
                    "PGCONNECT_TIMEOUT": str(_CLIENT_TIMEOUT),
                },
                capture_output=True,
                text=True,
                timeout=_CLIENT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def _ensure_role_sql(self) -> str:
        role = self._quote_ident(self.config.admin_user)
        name = self._sql_quote(self.config.admin_user)
        pw = self._sql_quote(self.config.root_password)
        return (
            "DO $$ BEGIN "
            f"IF EXISTS (SELECT FROM pg_roles WHERE rolname = {name}) THEN "
            f"ALTER ROLE {role} WITH LOGIN SUPERUSER PASSWORD {pw}; "
            "ELSE "
            f"CREATE ROLE {role} WITH LOGIN SUPERUSER PASSWORD {pw}; "
            "END IF; END $$;"
        )

    def _run_sql_as_superuser(self, sql: str) -> None:
        # Peer/trust auth lets the bench OS user bootstrap without sudo.
        # Linux needs our socket_dir; Homebrew already uses a user-owned socket.
        cmd = [
            self._psql() or "psql",
            "-p",
            str(self.config.port),
            "-v",
            "ON_ERROR_STOP=1",
            "-d",
            "postgres",
        ]
        if not is_macos():
            cmd[1:1] = ["-h", str(self.socket_dir)]
        subprocess.run(cmd, input=sql, text=True, check=True)

    def is_reachable(self) -> bool:
        ready = which("pg_isready")
        if ready:
            result = subprocess.run(
                [ready, "-h", self.config.host, "-p", str(self.config.port)], capture_output=True
            )
            return result.returncode == 0
        return self.is_running()

    def _server_binary(self, name: str) -> str:
        found = which(name)
        if found:
            return found
        candidates = sorted(_DEBIAN_POSTGRES_ROOT.glob(f"*/bin/{name}"), key=self._debian_version_key)
        if candidates:
            return str(candidates[-1])
        return name

    @staticmethod
    def _debian_version_key(binary: Path) -> tuple[int, ...]:
        version = binary.parent.parent.name
        return tuple(int(part) for part in version.split(".") if part.isdigit())

    def _psql(self) -> str | None:
        found = which("psql")
        if found:
            return found
        if is_macos():
            result = subprocess.run(
                ["brew", "--prefix", self._brew_package()], capture_output=True, text=True
            )
            if result.returncode == 0:
                candidate = Path(result.stdout.strip()) / "bin" / "psql"
                if candidate.exists():
                    return str(candidate)
        return None

    @staticmethod
    def _sql_quote(value: str) -> str:
        """Quote a value as a PostgreSQL string literal."""
        return "'" + value.replace("'", "''") + "'"

    @staticmethod
    def _quote_ident(value: str) -> str:
        """Quote an identifier (role name)."""
        return '"' + value.replace('"', '""') + '"'
