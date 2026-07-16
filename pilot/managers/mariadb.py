import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from pilot.config.mariadb_config import MariaDBConfig
from pilot.managers.user_database import UserOwnedDBManager
from pilot.managers.platform import is_macos, which
from pilot.utils import run_command

# One MariaDB server per bench user, shared by every bench they own — rootless,
# running as a single systemd --user unit. Fixed locations, no per-bench units.
_STATE_DIR = Path.home() / ".local" / "share" / "pilot" / "mariadb"
_CLIENT_TIMEOUT = 5


class MariaDBManager(UserOwnedDBManager):
    _UNIT_NAME = "pilot-mariadb.service"
    _DISPLAY_NAME = "MariaDB"
    _SYSTEM_PACKAGE = "mariadb-server"
    _BREW_FORMULA_BASE = "mariadb"
    _DEFAULT_VERSION = "11.8"

    def __init__(self, config: MariaDBConfig) -> None:
        self.config = config

    def data_dir(self) -> Path:
        return _STATE_DIR / "data"

    def pid_file(self) -> Path:
        return _STATE_DIR / "mysqld.pid"

    def socket_path(self) -> str:
        if self.config.socket_path:
            return self.config.socket_path
        return str(_STATE_DIR / "mysqld.sock")

    def is_installed(self) -> bool:
        # which() searches sbin too; mysqld/mariadbd live in /usr/sbin.
        return bool(which("mysqld") or which("mariadbd"))

    def is_provisioned(self) -> bool:
        if is_macos():
            return self.is_running() and not self.is_unsecured()
        return super().is_provisioned()

    def _provision_macos(self):
        if not self.is_running():
            self.start()
        self._wait_until_reachable()
        self.secure_installation()

    def provision(self) -> None:
        """Ensure the shared MariaDB server is installed, running and secured.
        Idempotent and safe for every bench to call — the first bench for this
        user provisions it, later benches just reuse the already-running server.
        """
        self.install()
        if is_macos():
            return self._provision_macos()

        if not self.is_provisioned():
            self._initialize_data_dir()
            self._install_unit()
            run_command(
                self._systemctl("enable", "--now", self._UNIT_NAME), env=self._systemctl_env()
            )

        elif not self.is_running():
            run_command(self._systemctl("start", self._UNIT_NAME), env=self._systemctl_env())

        self._wait_until_reachable()
        self.secure_installation()

    def _initialize_data_dir(self) -> None:
        self.data_dir().mkdir(parents=True, exist_ok=True)
        run_command(["mariadb-install-db", f"--datadir={self.data_dir()}", "--skip-test-db"])

    def _install_unit(self) -> None:
        mariadbd = which("mariadbd") or which("mysqld") or "/usr/sbin/mariadbd"
        content = (
            "[Unit]\n"
            "Description=MariaDB (pilot, user-owned)\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart={mariadbd} --datadir={self.data_dir()} --socket={self.socket_path()} "
            f"--port={self.config.port} --pid-file={self.pid_file()} --bind-address=127.0.0.1\n"
            "Restart=on-failure\n\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )
        unit_dir = self._user_unit_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        self.unit_path().write_text(content)
        run_command(self._systemctl("daemon-reload"), env=self._systemctl_env())

    def _wait_until_reachable(self, timeout: float = 30.0) -> None:
        """Poll until the server is active and reachable, so securing doesn't
        race the daemon's startup. Falls through on timeout — the next step
        surfaces a clear connection error."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._is_reachable():
                return
            time.sleep(0.5)

    def _is_reachable(self) -> bool:
        if not self.is_running():
            return False
        if is_macos():
            # Homebrew owns the socket location here, not socket_path() (our
            # own _STATE_DIR, only ever created for the Linux systemd unit) —
            # is_running() is the only signal we have.
            return True
        return Path(self.socket_path()).exists()

    def is_unsecured(self) -> bool:
        """True if the admin account has no password and is reachable via
        unix-socket auth (i.e. a fresh, not-yet-secured install). The bench
        user owns this server outright, so no privilege escalation is needed
        to connect as its admin account."""
        cmd = ["mariadb"]
        if not is_macos():
            cmd.append(f"--socket={self.socket_path()}")
        cmd += ["-u", self.config.admin_user, "--batch", "--skip-column-names", "-e", "SELECT 1"]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_CLIENT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def check_credentials(self, password: str | None = None) -> bool:
        """True if the admin user can connect with the given password (default:
        configured root password). Uses the ``mariadb`` client, not pymysql, so
        the zero-dep CLI works during init; password goes via MYSQL_PWD, not argv."""
        pw = self.config.root_password if password is None else password
        cmd = [
            "mariadb",
            f"--connect-timeout={_CLIENT_TIMEOUT}",
            "-u",
            self.config.admin_user,
            "--batch",
            "--skip-column-names",
        ]
        socket_path = self._detect_socket()
        if socket_path:
            cmd.append(f"--socket={socket_path}")
        else:
            cmd += ["-h", self.config.host, "-P", str(self.config.port)]
        cmd += ["-e", "SELECT 1"]
        try:
            result = subprocess.run(
                cmd,
                env={**os.environ, "MYSQL_PWD": pw},
                capture_output=True,
                text=True,
                timeout=_CLIENT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def secure_installation(self) -> None:
        """Ensure the configured admin account exists with the configured
        password, and apply some hardening. Idempotent — a no-op once
        check_credentials() already succeeds."""
        if self.check_credentials():
            return
        # admin_user can arrive from the (unauthenticated) setup wizard's
        # bench.toml, so it must be quoted exactly like a password — never
        # interpolated raw into SQL.
        user = self._sql_quote(self.config.admin_user)
        password = self._sql_quote(self.config.root_password)
        statements = [
            # CREATE...IF NOT EXISTS + ALTER covers both a fresh install (no
            # such account yet) and a server another bench already secured
            # with a different password.
            f"CREATE USER IF NOT EXISTS {user}@'localhost' IDENTIFIED BY {password};",
            f"ALTER USER {user}@'localhost' IDENTIFIED BY {password};",
            f"GRANT ALL PRIVILEGES ON *.* TO {user}@'localhost' WITH GRANT OPTION;",
            "DROP USER IF EXISTS ''@'localhost';",
            "DROP USER IF EXISTS ''@'%';",
            "DROP DATABASE IF EXISTS test;",
            "FLUSH PRIVILEGES;",
        ]
        self._run_sql_as_superuser("\n".join(statements))

    def _run_sql_as_superuser(self, sql: str) -> None:
        cmd = ["mariadb"]
        if not is_macos():
            # No explicit -u: this runs as the bench user directly —
            # mariadb-install-db (run earlier, also as the bench user) already
            # granted that exact OS username full unix_socket-authenticated
            # access, so no privilege escalation or pre-existing account is
            # required to bootstrap from here.
            cmd.append(f"--socket={self.socket_path()}")
        # On macOS, Homebrew's mariadb client already defaults to the same
        # socket its brew-managed server listens on; socket_path() (our own
        # _STATE_DIR) is never used there, so the client's own default applies.
        subprocess.run(cmd, input=sql, text=True, check=True)

    @staticmethod
    def _sql_quote(value: str) -> str:
        """Quote a value as a MariaDB string literal (escaping \\ and ')."""
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"

    @contextmanager
    def snapshot_lock(self):
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("FLUSH TABLES WITH READ LOCK")
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("UNLOCK TABLES")
            connection.close()

    def connect(self, password: str | None = None, cursorclass=None):
        """The one place that resolves host, port, admin user, and socket for an
        admin connection to this bench's MariaDB."""
        import pymysql

        return pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.admin_user,
            password=self.config.root_password if password is None else password,
            unix_socket=self._detect_socket() or None,
            cursorclass=cursorclass or pymysql.cursors.Cursor,
        )

    def _detect_socket(self) -> str:
        if self.config.socket_path:
            return self.config.socket_path
        if not is_macos() and Path(self.socket_path()).exists():
            return self.socket_path()
        return ""
