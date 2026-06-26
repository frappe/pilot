import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from pilot.config.mariadb_config import MariaDBConfig
from pilot.platform import (
    _privileged,
    get_package_manager,
    is_alpine,
    is_macos,
    service_command,
    service_disable_command,
    service_enable_command,
    service_running,
    which,
)
from pilot.utils import run_command

_MACOS_SOCKET_CANDIDATES = ["/tmp/mysql.sock", "/usr/local/var/mysql/mysql.sock"]
_LINUX_SOCKET_CANDIDATES = ["/var/run/mysqld/mysqld.sock", "/run/mysqld/mysqld.sock"]
# Alpine's mariadb package uses the conventional shared datadir.
_ALPINE_DATA_DIR = Path("/var/lib/mysql")
# Absolute paths we must never `rm -rf`, even if misconfigured as a datadir.
_PROTECTED_DATA_DIRS = frozenset(
    Path(p)
    for p in ("/", "/var", "/var/lib", "/var/lib/mysql", "/home", "/root", "/etc", "/usr", "/srv", "/opt", "/mnt", "/data")
)

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
        if not self.is_dedicated:
            return "mariadb"
        # systemd ships a mariadb@.service template; Alpine/OpenRC has no template,
        # so a dedicated instance runs a bench-generated `mariadb-<instance>` script.
        return f"mariadb-{self.config.instance}" if is_alpine() else f"mariadb@{self.config.instance}"

    def instance_socket(self) -> str:
        return self.config.socket_path or f"/run/mysqld/mysqld-{self.config.instance}.sock"

    def data_dir(self) -> str:
        # Sibling of /var/lib/mysql, never nested inside it - a legacy shared
        # server uses /var/lib/mysql as its datadir and would otherwise treat
        # /var/lib/mysql/<instance> as a phantom database. Also snapshotting and rollbacks
        # Wouldn't be bench independent
        return self.config.data_dir or f"/var/lib/mysql-{self.config.instance}"

    def service_is_active(self) -> bool:
        return service_running(self.service_unit())

    def is_installed(self) -> bool:
        # which() searches sbin too; mysqld/mariadbd live in /usr/sbin.
        return bool(which("mysqld") or which("mariadbd"))

    def install(self) -> None:
        if is_alpine():
            self._install_alpine()
            return
        if self.is_installed():
            return
        package_manager = get_package_manager()
        if is_macos():
            package_manager.install(self._brew_package())
            return
        self._setup_apt_repo()
        package_manager.update()
        package_manager.install("mariadb-server", "mariadb-client")

    def _install_alpine(self) -> None:
        # Alpine ships no MariaDB official apk repo; the distro package (11.x) is
        # what we use. Unlike Debian, it neither initialises the datadir nor
        # enables the service, so do both here. Idempotent — safe to re-run.
        if not self.is_installed():
            get_package_manager().install("mariadb", "mariadb-client")
        # A dedicated bench runs its own instance (provisioned separately), so it
        # never touches the shared server — don't initialise or enable it.
        if self.is_dedicated:
            return
        self._initialize_data_dir()
        run_command(service_enable_command("mariadb"))

    def _initialize_data_dir(self) -> None:
        if (_ALPINE_DATA_DIR / "mysql").is_dir():
            return
        run_command(_privileged([
            "mariadb-install-db",
            "--user=mysql",
            f"--datadir={_ALPINE_DATA_DIR}",
            "--skip-test-db",
        ]))

    def start(self) -> None:
        if is_macos():
            run_command(["brew", "services", "start", self._brew_package()])
        else:
            run_command(service_command("start", self.service_unit()))

    def restart(self) -> None:
        if is_macos():
            run_command(["brew", "services", "restart", self._brew_package()])
        else:
            run_command(service_command("restart", self.service_unit()))

    def stop(self) -> None:
        if is_macos():
            run_command(["brew", "services", "stop", self._brew_package()])
        else:
            run_command(service_command("stop", self.service_unit()))

    def remove_instance(self) -> None:
        """Tear down this bench's dedicated MariaDB instance — the inverse of
        provision_instance. Best-effort and idempotent: absent units/files are
        skipped. No-op for shared (non-dedicated) setups, e.g. macOS. Used when
        dropping a bench."""
        if not self.is_dedicated or is_macos():
            return
        if is_alpine():
            self._remove_instance_openrc()
        else:
            self._remove_instance_systemd()
        self._remove_data_dir()

    def _remove_instance_systemd(self) -> None:
        instance = self.config.instance
        service = self.service_unit()  # mariadb@<instance>
        for cmd in (service_command("stop", service), service_disable_command(service)):
            try:
                run_command(cmd)
            except Exception:
                pass
        override_dir = f"/etc/systemd/system/mariadb@{instance}.service.d"
        run_command(_privileged(["rm", "-rf", override_dir]))
        run_command(_privileged(["rm", "-f", f"{_CONF_DIR}/99-bench-{instance}.cnf"]))
        try:
            run_command(["sudo", "systemctl", "daemon-reload"])
        except Exception:
            pass

    def _remove_instance_openrc(self) -> None:
        service = self.service_unit()  # mariadb-<instance>
        for cmd in (service_command("stop", service), service_disable_command(service)):
            try:
                run_command(cmd)
            except Exception:
                pass
        run_command(_privileged(["rm", "-f", f"/etc/init.d/{service}", f"/var/log/{service}.log"]))

    def _remove_data_dir(self) -> None:
        # Only a dedicated instance owns a removable datadir; the shared server's
        # is never ours to wipe. Guard against any shallow or protected system
        # path so a malformed config can't delete data outside the bench.
        if not self.is_dedicated:
            return
        data_dir = self.data_dir()
        resolved = Path(data_dir).resolve()
        if resolved in _PROTECTED_DATA_DIRS or len(resolved.parts) < 3:
            return
        run_command(_privileged(["rm", "-rf", data_dir]))

    def stop_shared(self) -> None:
        """Stop and disable the shared mariadb service.

        Called after a fresh package install for dedicated-instance benches:
        the package manager auto-starts the shared service on port 3306, which
        would collide with the dedicated instance's port before
        provision_instance runs.
        """
        try:
            run_command(service_command("stop", "mariadb"))
            run_command(service_disable_command("mariadb"))
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

    def configure_shared_port(self) -> bool:
        """Write a drop-in config so the shared mariadb service listens on the
        configured port.  Returns True if a config was written (the caller must
        restart the service to apply it).  No-op when port is the default 3306,
        when this is a dedicated instance, or on macOS (Homebrew config paths vary), or when
        the shared port override already exists."""
        conf_dir = "/etc/my.cnf.d" if is_alpine() else _CONF_DIR
        # Load shared before we load dedicated (allowing dedicated to override)
        conf_path = f"{conf_dir}/98-bench-shared-port.cnf"

        if self.is_dedicated or self.config.port == 3306 or is_macos() or Path(conf_path).exists():
            return False
        content = f"[mariadbd]\nport = {self.config.port}\n"
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cnf", delete=False) as f:
            f.write(content)
            tmp = f.name
        try:
            run_command(["sudo", "install", "-m", "644", tmp, conf_path])
        finally:
            os.unlink(tmp)
        return True

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

        if is_alpine():
            self._provision_instance_openrc(staging_dir)
            return

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

    def _provision_instance_openrc(self, staging_dir: Path) -> None:
        """Create, configure, start and secure this bench's MariaDB instance on
        Alpine/OpenRC. Idempotent — safe to re-run.

        Alpine has no ``mariadb@.service`` template, so we generate a
        ``supervise-daemon`` init script that runs a second ``mariadbd`` with this
        bench's datadir/socket/port — the OpenRC counterpart of the systemd
        template path. Explicit command-line flags override the shared
        ``[mariadbd]`` group in ``/etc/my.cnf.d``, so no per-instance option file
        (or fragile include-ordering) is needed.
        """
        instance = self.config.instance
        data_dir = self.data_dir()
        socket = self.instance_socket()
        pid_file = f"/run/mysqld/mysqld-{instance}.pid"

        # Runtime dir for the per-instance socket and pid file.
        run_command(_privileged(["install", "-d", "-m", "755", "-o", "mysql", "-g", "mysql", "/run/mysqld"]))
        # Datadir, owned by mysql, initialised only if empty (a re-run keeps data).
        run_command(_privileged(["install", "-d", "-m", "750", "-o", "mysql", "-g", "mysql", data_dir]))
        if not (Path(data_dir) / "mysql").is_dir():
            run_command(_privileged([
                "mariadb-install-db", "--user=mysql", f"--datadir={data_dir}", "--skip-test-db",
            ]))

        self._install_openrc_mariadb_service(staging_dir, data_dir, socket, pid_file)
        run_command(service_enable_command(self.service_unit()))
        run_command(service_command("start", self.service_unit()))
        self._wait_until_reachable()
        self.secure_installation()

    def _install_openrc_mariadb_service(self, staging_dir: Path, data_dir: str, socket: str, pid_file: str) -> None:
        """Render and install the instance's OpenRC init script into /etc/init.d."""
        instance = self.config.instance
        service = self.service_unit()
        mariadbd = which("mariadbd") or "/usr/sbin/mariadbd"
        args = (
            f"--datadir={data_dir} --socket={socket} --port={self.config.port} "
            f"--pid-file={pid_file} --bind-address=127.0.0.1"
        )
        script = "\n".join([
            "#!/sbin/openrc-run",
            f"# MariaDB instance for bench {instance} — generated by bench, do not edit",
            f'description="MariaDB ({instance})"',
            "supervisor=supervise-daemon",
            f'command="{mariadbd}"',
            f'command_args="{args}"',
            'command_user="mysql:mysql"',
            f'pidfile="/run/{service}.pid"',
            f'output_log="/var/log/{service}.log"',
            f'error_log="/var/log/{service}.log"',
            "respawn_delay=5",
            "",
            "depend() {",
            "\tuse net",
            "\tafter firewall",
            "}",
        ]) + "\n"
        staged_dir = staging_dir / "mariadb"
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged = staged_dir / service
        staged.write_text(script)
        run_command(_privileged(["install", "-m", "0755", str(staged), f"/etc/init.d/{service}"]))
        # supervise-daemon opens output_log/error_log *after* dropping to mysql, so
        # the file must already exist and be mysql-writable — otherwise the daemon
        # silently fails to start (it can't create a file in root-owned /var/log).
        run_command(_privileged(["install", "-m", "0644", "-o", "mysql", "-g", "mysql", "/dev/null", f"/var/log/{service}.log"]))

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

    def is_unsecured(self) -> bool:
        """True if the admin account has no password and is reachable via
        unix-socket auth (i.e. a fresh, not-yet-secured install).  Uses the
        same privileged connection path as _run_sql_as_superuser — no MYSQL_PWD."""
        cmd = ["mariadb"] if is_macos() else _privileged(["mariadb"])
        if self.is_dedicated:
            cmd.append(f"--socket={self.instance_socket()}")
        cmd += ["-u", self.config.admin_user, "--batch", "--skip-column-names", "-e", "SELECT 1"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0

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
        cmd = ["mariadb"] if is_macos() else _privileged(["mariadb"])
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
