import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

from bench_cli.config.mariadb_config import MariaDBConfig
from bench_cli.platform import get_package_manager, is_macos
from bench_cli.utils import run_command

_MACOS_SOCKET_CANDIDATES = ["/tmp/mysql.sock", "/usr/local/var/mysql/mysql.sock"]
_LINUX_SOCKET_CANDIDATES = ["/var/run/mysqld/mysqld.sock", "/run/mysqld/mysqld.sock"]

DEFAULT_VERSION = "11.8"
_REPO_SETUP_URL = "https://r.mariadb.com/downloads/mariadb_repo_setup"


class MariaDBManager:
    def __init__(self, config: MariaDBConfig) -> None:
        self.config = config

    def is_installed(self) -> bool:
        return bool(shutil.which("mysqld") or shutil.which("mariadbd"))

    def install(self) -> None:
        if self.is_installed():
            return
        package_manager = get_package_manager()
        if is_macos():
            package_manager.install(self._brew_package())
            return
        self._setup_apt_repo()
        package_manager.update()
        package_manager.install("mariadb-server", "mariadb-client")

    def is_running(self) -> bool:
        if is_macos():
            result = subprocess.run(["brew", "services", "list"], capture_output=True, text=True)
            return any(
                line.split()[1] == "started"
                for line in result.stdout.splitlines()
                if line.split() and line.split()[0].startswith("mariadb")
            )
        result = subprocess.run(["systemctl", "is-active", "mariadb"], capture_output=True, text=True)
        return result.returncode == 0

    def start(self) -> None:
        if is_macos():
            if not self.is_running():
                run_command(["brew", "services", "start", self._brew_package()])
        else:
            run_command(["sudo", "systemctl", "start", "mariadb"])

    def stop(self) -> None:
        if is_macos():
            run_command(["brew", "services", "stop", self._brew_package()])
        else:
            run_command(["sudo", "systemctl", "stop", "mariadb"])

    def _version(self) -> str:
        return self.config.version or DEFAULT_VERSION

    def _brew_package(self) -> str:
        return self._installed_brew_formula() or f"mariadb@{self._version()}"

    def _installed_brew_formula(self) -> str | None:
        """Return the mariadb formula Homebrew already manages (e.g. 'mariadb@10.6').

        When bench.toml doesn't pin a version, start/stop must target whatever
        brew actually installed — assuming plain 'mariadb' fails when only a
        versioned formula like mariadb@10.6 is present.
        """
        result = subprocess.run(["brew", "list", "--formula"], capture_output=True, text=True)
        if result.returncode != 0:
            return None
        formulae = result.stdout.split()
        if "mariadb" in formulae:
            return "mariadb"
        return next((f for f in formulae if f.startswith("mariadb@")), None)

    def _setup_apt_repo(self) -> None:
        """Add MariaDB's official APT repository pinned to the target version.

        Ubuntu/Debian ship far older MariaDB than the 11.8 LTS series
        """
        script = subprocess.run(
            ["curl", "-LsS", _REPO_SETUP_URL],
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["sudo", "bash", "-s", "--", f"--mariadb-server-version=mariadb-{self._version()}"],
            input=script.stdout,
            check=True,
        )

    def check_credentials(self, password: str | None = None) -> bool:
        """Return True if we can connect as the admin user with the given
        password (defaults to the configured root password)."""
        import pymysql

        try:
            connection = self._connect(password)
        except pymysql.Error:
            return False
        connection.close()
        return True

    def secure_installation(self) -> None:
        """
        Set the root password and apply some hardening.
        Will only work after fresh installs
        """
        if self.check_credentials():
            return
        user = self.config.admin_user
        statements = [
            f"ALTER USER '{user}'@'localhost' IDENTIFIED BY {self._sql_quote(self.config.root_password)};",
            "DROP USER IF EXISTS ''@'localhost';",
            "DROP USER IF EXISTS ''@'%';",
            "DROP DATABASE IF EXISTS test;",
            "FLUSH PRIVILEGES;",
        ]
        self._run_sql_as_superuser("\n".join(statements))

    def _run_sql_as_superuser(self, sql: str) -> None:
        cmd = ["mariadb"] if is_macos() else ["sudo", "mariadb"]
        subprocess.run(cmd, input=sql, text=True, check=True)

    @staticmethod
    def _sql_quote(value: str) -> str:
        """Quote a value as a MariaDB string literal (escaping \\ and ')."""
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"

    def kill_process(self, process_id: int) -> None:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("KILL %s", (process_id,))
        finally:
            connection.close()

    @contextmanager
    def snapshot_lock(self):
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("FLUSH TABLES WITH READ LOCK")
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("UNLOCK TABLES")
            connection.close()

    def _connect(self, password: str | None = None):
        import pymysql

        return pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.admin_user,
            password=self.config.root_password if password is None else password,
            unix_socket=self._detect_socket() or None,
        )

    def _detect_socket(self) -> str:
        if self.config.socket_path:
            return self.config.socket_path
        candidates = _MACOS_SOCKET_CANDIDATES if is_macos() else _LINUX_SOCKET_CANDIDATES
        for path in candidates:
            if Path(path).exists():
                return path
        return ""
