import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from bench_cli.config.mariadb_config import MariaDBConfig
from bench_cli.platform import get_package_manager, is_macos, which
from bench_cli.utils import run_command

_MACOS_SOCKET_CANDIDATES = ["/tmp/mysql.sock", "/usr/local/var/mysql/mysql.sock"]
_LINUX_SOCKET_CANDIDATES = ["/var/run/mysqld/mysqld.sock", "/run/mysqld/mysqld.sock"]

DEFAULT_VERSION = "11.8"
_REPO_SETUP_URL = "https://r.mariadb.com/downloads/mariadb_repo_setup"


# Instance option groups go in mariadb.conf.d/ (read AFTER conf.d/ per
# /etc/mysql/my.cnf) with a 99- prefix so they sort after 50-server.cnf. This
# ordering matters: 50-server.cnf's base [mariadbd] sets pid-file, so an
# instance file read earlier (e.g. in conf.d/) would have its pid-file silently
# overridden back to the shared default and collide. Read last, our suffixed
# [mariadbd.<instance>] group wins for pid-file/socket/port/datadir.
_CONF_DIR = "/etc/mysql/mariadb.conf.d"


class MariaDBManager:
    def __init__(self, config: MariaDBConfig) -> None:
        self.config = config

    @property
    def is_dedicated(self) -> bool:
        """True when this bench runs its own mariadb@<instance> rather than the
        shared system MariaDB (legacy)."""
        return bool(self.config.instance)

    def service_unit(self) -> str:
        return f"mariadb@{self.config.instance}" if self.is_dedicated else "mariadb"

    def instance_socket(self) -> str:
        return self.config.socket_path or f"/run/mysqld/mysqld-{self.config.instance}.sock"

    def data_dir(self) -> str:
        # Sibling of /var/lib/mysql, never nested inside it - a legacy shared
        # server uses /var/lib/mysql as its datadir and would otherwise treat
        # /var/lib/mysql/<instance> as a phantom database. Also snapshotting and rollbacks
        # Wouldn't be bench independent
        return self.config.data_dir or f"/var/lib/mysql-{self.config.instance}"

    def service_is_active(self) -> bool:
        result = subprocess.run(["systemctl", "is-active", "--quiet", self.service_unit()])
        return result.returncode == 0

    def is_installed(self) -> bool:
        # which() searches sbin too; mysqld/mariadbd live in /usr/sbin.
        return bool(which("mysqld") or which("mariadbd"))

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

    def start(self) -> None:
        if is_macos():
            run_command(["brew", "services", "start", self._brew_package()])
        else:
            run_command(["sudo", "systemctl", "start", self.service_unit()])

    def stop(self) -> None:
        if is_macos():
            run_command(["brew", "services", "stop", self._brew_package()])
        else:
            run_command(["sudo", "systemctl", "stop", self.service_unit()])

    def stop_shared(self) -> None:
        """Stop and disable the shared mariadb service.

        Called after a fresh package install for dedicated-instance benches:
        apt auto-starts the shared service on port 3306, which would collide
        with the dedicated instance's port before provision_instance runs.
        """
        try:
            run_command(["sudo", "systemctl", "stop", "mariadb"])
            run_command(["sudo", "systemctl", "disable", "mariadb"])
        except Exception:
            pass

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

    def provision_instance(self, staging_dir: Path) -> None:
        """Create, configure, start and secure this bench's MariaDB instance
        (mariadb@<instance>). Idempotent — safe to re-run.

        Uses the packaged ``mariadb@.service`` template, whose default
        ``--defaults-group-suffix=.%I`` reads the ``[mariadbd.<instance>]`` group
        we install, and whose ``ExecStartPre`` runs ``mariadb-install-db`` to
        initialise the datadir. We only stage the option group, pre-create the
        (mysql-owned) datadir, start, and secure.

        Ordering matters: the instance must be running and listening on its own
        socket/port *before* securing it — a fresh instance isn't up until we
        start it, and only then can secure_installation set the root password
        (the same flow a fresh shared install uses).
        """
        if not self.is_dedicated:
            raise RuntimeError("provision_instance called for a bench without a dedicated mariadb.instance")

        # Runtime dir for the per-instance socket and pid file (also created by
        # systemd-tmpfiles at boot; ensured here for first provisioning).
        run_command(["sudo", "install", "-d", "-m", "755", "-o", "mysql", "-g", "mysql", "/run/mysqld"])

        self._write_systemd_override(staging_dir)
        self._write_instance_config(staging_dir)

        # The unit runs as User=mysql, so the datadir must exist and be owned by
        # mysql before its ExecStartPre mariadb-install-db can populate it.
        run_command(["sudo", "install", "-d", "-m", "750", "-o", "mysql", "-g", "mysql", self.data_dir()])

        run_command(["sudo", "systemctl", "enable", "--now", self.service_unit()])
        self._wait_until_reachable()

        self.secure_installation()

    def _write_systemd_override(self, staging_dir: Path) -> None:
        """Pin the instance's option-group suffix to the *escaped* unit name (%i).

        The packaged ``mariadb@.service`` runs mariadbd with
        ``--defaults-group-suffix=.%I``. ``%I`` is systemd's *unescaped*
        specifier, and systemd encodes ``/`` as ``-``: for ``mariadb@my-bench``
        it expands to ``my/bench``, so mariadbd looks for ``[mariadbd.my/bench]``
        and never finds the ``[mariadbd.my-bench]`` group we install. The whole
        instance config (datadir/socket/port) is then silently ignored and the
        server falls back to the shared /var/lib/mysql, colliding with the
        system MariaDB. ``%i`` is the literal unit name, so it matches our group
        verbatim and keeps dashes in bench names working.
        """
        instance = self.config.instance
        override_dir = f"/etc/systemd/system/mariadb@{instance}.service.d"
        content = "[Service]\nEnvironment=MYSQLD_MULTI_INSTANCE=--defaults-group-suffix=.%i\n"
        staged_dir = staging_dir / "mariadb"
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged = staged_dir / f"override-{instance}.conf"
        staged.write_text(content)
        run_command(["sudo", "install", "-d", "-m", "755", override_dir])
        run_command(["sudo", "cp", str(staged), f"{override_dir}/override.conf"])
        run_command(["sudo", "systemctl", "daemon-reload"])

    def _write_instance_config(self, staging_dir: Path) -> None:
        """Render the instance's option group and install it under mariadb.conf.d/.

        The [mariadbd.<instance>] suffixed group is only applied when mariadbd is
        started with --defaults-group-suffix=.<instance> (the packaged template
        unit), so the shared default server ignores it. The 99- prefix ensures it
        is read after 50-server.cnf, otherwise the base [mariadbd] pid-file would
        override the instance's.
        """
        instance = self.config.instance
        content = (
            f"[mariadbd.{instance}]\n"
            f"datadir = {self.data_dir()}\n"
            f"socket = {self.instance_socket()}\n"
            f"port = {self.config.port}\n"
            f"pid-file = /run/mysqld/mysqld-{instance}.pid\n"
            "bind-address = 127.0.0.1\n"
        )
        staged_dir = staging_dir / "mariadb"
        staged_dir.mkdir(parents=True, exist_ok=True)
        filename = f"99-bench-{instance}.cnf"
        staged = staged_dir / filename
        staged.write_text(content)
        run_command(["sudo", "cp", str(staged), f"{_CONF_DIR}/{filename}"])

    def _wait_until_reachable(self, timeout: float = 30.0) -> None:
        """Poll until the instance is active and its socket exists, so securing
        doesn't race the daemon's startup. Falls through on timeout — the next
        step surfaces a clear connection error."""
        socket = self.instance_socket()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.service_is_active() and Path(socket).exists():
                return
            time.sleep(0.5)

    def check_credentials(self, password: str | None = None) -> bool:
        """True if the admin user can connect with the given password (default:
        configured root password). Uses the ``mariadb`` client, not pymysql, so
        the zero-dep CLI works during init; password goes via MYSQL_PWD, not argv."""
        pw = self.config.root_password if password is None else password
        cmd = ["mariadb", "-u", self.config.admin_user, "--batch", "--skip-column-names"]
        socket = self._detect_socket()
        if socket:
            cmd.append(f"--socket={socket}")
        else:
            cmd += ["-h", self.config.host, "-P", str(self.config.port)]
        cmd += ["-e", "SELECT 1"]
        result = subprocess.run(cmd, env={**os.environ, "MYSQL_PWD": pw}, capture_output=True, text=True)
        return result.returncode == 0

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
        if self.is_dedicated:
            # Target this bench's instance socket rather than the default one.
            cmd.append(f"--socket={self.instance_socket()}")
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
        if self.is_dedicated:
            return self.instance_socket()
        candidates = _MACOS_SOCKET_CANDIDATES if is_macos() else _LINUX_SOCKET_CANDIDATES
        for path in candidates:
            if Path(path).exists():
                return path
        return ""
