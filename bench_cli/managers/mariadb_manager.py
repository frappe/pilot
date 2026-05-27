import shutil
from pathlib import Path

from bench_cli.config.mariadb_config import MariaDBConfig
from bench_cli.platform import get_package_manager, is_macos
from bench_cli.utils import run_command

_MACOS_SOCKET_CANDIDATES = ["/tmp/mysql.sock", "/usr/local/var/mysql/mysql.sock"]
_LINUX_SOCKET_CANDIDATES = ["/var/run/mysqld/mysqld.sock", "/run/mysqld/mysqld.sock"]


class MariaDBManager:
    def __init__(self, config: MariaDBConfig) -> None:
        self.config = config

    def is_installed(self) -> bool:
        return bool(shutil.which("mysqld") or shutil.which("mariadbd"))

    def install(self) -> None:
        if self.is_installed():
            return
        package_manager = get_package_manager()
        package = self._brew_package() if is_macos() else self._apt_package()
        package_manager.install(package)

    def start(self) -> None:
        if is_macos():
            run_command(["brew", "services", "start", self._brew_package()])
        else:
            run_command(["sudo", "systemctl", "start", "mariadb"])

    def _brew_package(self) -> str:
        if self.config.version:
            return f"mariadb@{self.config.version}"
        return "mariadb"

    def _apt_package(self) -> str:
        if self.config.version:
            return f"mariadb-server-{self.config.version}"
        return "mariadb-server"

    def _grant_host(self) -> str:
        """Return the MySQL grant host for CREATE USER / GRANT statements.

        Use '%' when connecting over TCP so that the site DB user can reach
        MariaDB from 127.0.0.1 (TCP loopback).  Fall back to 'localhost'
        only when a unix socket is detected — in MySQL's privilege tables
        'localhost' matches socket connections exclusively.
        """
        return "localhost" if self._detect_socket() else "%"

    def create_user(self, username: str, password: str, db_name: str) -> None:
        """Create the DB user and grant all privileges with the correct host scope.

        Frappe's new-site always creates the user as @'localhost', which
        breaks TCP connections (127.0.0.1 != localhost in privilege tables).
        This method uses the grant host derived from _grant_host() so TCP
        connections work when no unix socket is available.
        """
        grant_host = self._grant_host()
        sql = (
            f"CREATE USER IF NOT EXISTS '{username}'@'{grant_host}'"
            f" IDENTIFIED BY '{password}';"
            f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{username}'@'{grant_host}';"
            "FLUSH PRIVILEGES;"
        )
        mysql_bin = shutil.which("mariadb") or shutil.which("mysql") or "mysql"
        cmd = [mysql_bin, f"-u{self.config.admin_user}"]
        if self.config.root_password:
            cmd.append(f"-p{self.config.root_password}")
        socket_path = self._detect_socket()
        if socket_path:
            cmd.append(f"--socket={socket_path}")
        else:
            cmd += [f"-h{self.config.host}", f"-P{self.config.port}"]
        cmd += ["-e", sql]
        run_command(cmd)

    def _detect_socket(self) -> str:
        if self.config.socket_path:
            return self.config.socket_path
        candidates = _MACOS_SOCKET_CANDIDATES if is_macos() else _LINUX_SOCKET_CANDIDATES
        for path in candidates:
            if Path(path).exists():
                return path
        return ""
