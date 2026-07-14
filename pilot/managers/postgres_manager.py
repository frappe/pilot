from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from pathlib import Path

from pilot.config.postgres_config import PostgresConfig
from pilot.package_managers import get_package_manager
from pilot.platform import (
    _privileged,
    is_alpine,
    is_linux,
    is_macos,
    service_command,
    service_enable_command,
    service_running,
    which,
)
from pilot.utils import run_command

DEFAULT_VERSION = "16"
_SERVICE = "postgresql"
# The shared system server owns 5432; dedicated clusters start above it.
_SHARED_PORT = 5432


def supports_dedicated_postgres() -> bool:
    """Dedicated clusters use postgresql-common (pg_createcluster) under systemd.
    Alpine (OpenRC) and macOS run PostgreSQL benches on the shared server."""
    return is_linux() and not is_alpine()


def pick_dedicated_postgres_port(bench_path: Path) -> int:
    """Smallest free port at/above 5433 for a new dedicated cluster — clear of the
    shared server (5432), sibling clusters, and anything currently listening."""
    from pilot.utils import iter_sibling_benches

    used = {_SHARED_PORT}
    for _, config in iter_sibling_benches(bench_path):
        postgres = getattr(config, "postgres", None)
        if postgres and postgres.instance:
            used.add(postgres.port)
    port = _SHARED_PORT + 1
    while port in used or _port_is_live(port):
        port += 1
    return port


def _port_is_live(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


class PostgresManager:
    """Manage the PostgreSQL server bench installs and provisions.

    A bench either shares the system server (isolated per-database) or, when
    postgres.instance is set, runs its own cluster on its own port. Dedicated
    clusters use postgresql-common (pg_createcluster) and so need systemd.
    """

    def __init__(self, config: PostgresConfig) -> None:
        self.config = config

    @property
    def is_dedicated(self) -> bool:
        return bool(self.config.instance)

    def _detected_version(self) -> str:
        """Major version to create a cluster with: the configured version, else the
        newest server apt installed under /usr/lib/postgresql/<major>."""
        if self.config.version:
            return self.config.version.split(".")[0]
        base = Path("/usr/lib/postgresql")
        if base.is_dir():
            majors = sorted(int(p.name) for p in base.iterdir() if p.name.isdigit())
            if majors:
                return str(majors[-1])
        return DEFAULT_VERSION

    def _cluster_row(self) -> list[str]:
        """This instance's `pg_lsclusters` row (Ver Cluster Port Status …), or []."""
        result = subprocess.run(["pg_lsclusters", "--no-header"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == self.config.instance:
                return parts
        return []

    def _cluster_version(self) -> str | None:
        row = self._cluster_row()
        return row[0] if row else None

    def service_unit(self) -> str:
        if not self.is_dedicated:
            return _SERVICE
        return f"{_SERVICE}@{self._cluster_version() or self._detected_version()}-{self.config.instance}"

    # ── install ──────────────────────────────────────────────────────────────

    def is_installed(self) -> bool:
        return bool(which("psql") or which("postgres") or which("initdb"))

    def install(self) -> None:
        if self.is_installed():
            return
        if is_macos():
            get_package_manager().install(self._brew_package())
            return
        if is_alpine():
            # Alpine ships only versioned packages (no unversioned `postgresql`).
            get_package_manager().install(*self.alpine_packages())
            return
        get_package_manager().install("postgresql", "postgresql-client")

    def alpine_packages(self) -> list[str]:
        major = self._alpine_major()
        return [f"postgresql{major}", f"postgresql{major}-client"]

    def alpine_dev_package(self) -> str:
        """libpq build headers for psycopg — versioned on Alpine."""
        return f"postgresql{self._alpine_major()}-dev"

    def _alpine_major(self) -> str:
        """The PostgreSQL major to install on Alpine: the configured version, else
        the newest apk offers (Alpine has no unversioned postgresql package)."""
        if self.config.version:
            return self.config.version.split(".")[0]
        result = subprocess.run(["apk", "list", "--available", "postgresql*"], capture_output=True, text=True)
        majors = sorted({int(m) for m in re.findall(r"\bpostgresql(\d+)-", result.stdout)})
        return str(majors[-1]) if majors else DEFAULT_VERSION

    def _version(self) -> str:
        return self.config.version or DEFAULT_VERSION

    def _brew_package(self) -> str:
        return self._installed_brew_formula() or f"postgresql@{self._version()}"

    def _installed_brew_formula(self) -> str | None:
        """The postgresql formula Homebrew already manages, so start/stop target
        whatever is installed rather than assuming a version."""
        result = subprocess.run(["brew", "list", "--formula"], capture_output=True, text=True)
        if result.returncode != 0:
            return None
        formulae = result.stdout.split()
        if "postgresql" in formulae:
            return "postgresql"
        return next((f for f in formulae if f.startswith("postgresql@")), None)

    # ── service control ──────────────────────────────────────────────────────

    def is_running(self) -> bool:
        if self.is_dedicated:
            return self._cluster_status() == "online"
        return self._brew_service_running() if is_macos() else service_running(_SERVICE)

    def start(self) -> None:
        if self.is_dedicated:
            self._ctlcluster("start")
        elif is_macos():
            run_command(["brew", "services", "start", self._brew_package()])
        else:
            run_command(service_command("start", _SERVICE))

    def restart(self) -> None:
        if self.is_dedicated:
            self._ctlcluster("restart")
        elif is_macos():
            run_command(["brew", "services", "restart", self._brew_package()])
        else:
            run_command(service_command("restart", _SERVICE))

    def stop(self) -> None:
        if self.is_dedicated:
            self._ctlcluster("stop")
        elif is_macos():
            run_command(["brew", "services", "stop", self._brew_package()])
        else:
            run_command(service_command("stop", _SERVICE))

    def enable(self) -> None:
        # `brew services start` already persists across logins. The system
        # postgresql service auto-starts every cluster (shared or dedicated).
        if is_macos():
            return
        try:
            run_command(service_enable_command(_SERVICE))
        except Exception:
            pass

    def _ctlcluster(self, action: str) -> None:
        # --skip-systemctl-redirect runs pg_ctl directly instead of going through
        # `systemctl start postgresql@<ver>-<cluster>`, whose templated unit fails
        # ("Assertion failed on job") in container/CI environments. Boot autostart
        # still works via the enabled postgresql meta-service (start.conf=auto).
        version = self._cluster_version() or self._detected_version()
        run_command(_privileged(["pg_ctlcluster", "--skip-systemctl-redirect", version, self.config.instance, action]))

    def _cluster_status(self) -> str | None:
        row = self._cluster_row()
        return row[3] if len(row) >= 4 else None

    def _brew_service_running(self) -> bool:
        result = subprocess.run(["brew", "services", "list"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            parts = line.split()
            if parts and parts[0] == self._brew_package() and "started" in parts:
                return True
        return False

    # ── provisioning ─────────────────────────────────────────────────────────

    def provision(self) -> None:
        """Install, start, enable and secure the server. Idempotent — safe to re-run."""
        self.install()
        if self.is_dedicated:
            self._provision_instance()
        else:
            if is_alpine():
                self._ensure_alpine_cluster()
            self.enable()
            if not self.is_running():
                self.start()
        self._wait_until_reachable()
        self.secure()

    def _provision_instance(self) -> None:
        """Create (or restart) this bench's dedicated cluster on its own port via
        postgresql-common, then enable autostart. systemd Linux only."""
        if not supports_dedicated_postgres():
            raise RuntimeError("Dedicated PostgreSQL clusters require systemd (postgresql-common); use the shared server instead.")
        if not self._cluster_row():
            run_command(_privileged([
                "pg_createcluster", self._detected_version(), self.config.instance,
                "-p", str(self.config.port),
            ]))
        # Start directly (not via systemd's templated unit — see _ctlcluster).
        if not self.is_running():
            self.start()
        self.enable()

    def remove_instance(self) -> None:
        """Stop and delete this bench's dedicated cluster. Best-effort and
        idempotent; a no-op for shared benches."""
        if not self.is_dedicated or not supports_dedicated_postgres():
            return
        version = self._cluster_version()
        if not version:
            return
        run_command(_privileged(["pg_dropcluster", version, self.config.instance, "--stop"]))

    def secure(self) -> None:
        """Ensure the admin role exists with the configured password so frappe can
        connect over TCP. Idempotent: a no-op once credentials work."""
        if not self.config.root_password:
            print(
                "  postgres.root_password is empty — skipping superuser setup. "
                "Set it in Settings before creating PostgreSQL sites."
            )
            return
        if self.check_credentials():
            return
        self._run_sql_as_superuser(self._ensure_role_sql())
        if not self.check_credentials():
            raise RuntimeError(
                f"PostgreSQL is installed but bench could not authenticate as '{self.config.admin_user}' "
                "over TCP. Ensure the server's pg_hba.conf allows password auth from localhost, or set "
                "postgres.root_password to the existing superuser password."
            )

    def check_credentials(self, password: str | None = None) -> bool:
        """True if the admin user can connect over TCP with the given password
        (default: configured root password). Uses psql so the zero-dep CLI works
        during init; the password goes via PGPASSWORD, not argv."""
        pw = self.config.root_password if password is None else password
        psql = self._psql()
        if not psql:
            return False
        cmd = [psql, "-h", self.config.host, "-p", str(self.config.port), "-U", self.config.admin_user, "-d", "postgres", "-tAc", "SELECT 1"]
        result = subprocess.run(cmd, env={**os.environ, "PGPASSWORD": pw}, capture_output=True, text=True)
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
        # The bootstrap superuser is the `postgres` OS account on Linux (peer auth
        # over the local socket) and the current user on macOS (Homebrew). -p
        # targets this bench's cluster — 5432 for shared, its own port if dedicated.
        cmd = ["sudo", "-u", "postgres", "psql"] if is_linux() else [self._psql() or "psql"]
        cmd += ["-p", str(self.config.port), "-v", "ON_ERROR_STOP=1", "-d", "postgres"]
        subprocess.run(cmd, input=sql, text=True, check=True)

    def _wait_until_reachable(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._accepting_connections():
                return
            time.sleep(0.5)

    def _accepting_connections(self) -> bool:
        ready = which("pg_isready")
        if ready:
            result = subprocess.run([ready, "-h", self.config.host, "-p", str(self.config.port)], capture_output=True)
            return result.returncode == 0
        return self.is_running()

    def _ensure_alpine_cluster(self) -> None:
        """apk neither initialises the data dir nor enables the service.
        Initialise the default cluster once; safe to re-run. Best-effort: assumes
        the conventional /var/lib/postgresql/<version>/data layout."""
        data_dir = Path(f"/var/lib/postgresql/{self._version()}/data")
        if (data_dir / "PG_VERSION").exists():
            return
        run_command(_privileged(["install", "-d", "-m", "700", "-o", "postgres", "-g", "postgres", str(data_dir)]))
        run_command(["sudo", "-u", "postgres", "initdb", "-D", str(data_dir)])

    def _psql(self) -> str | None:
        found = which("psql")
        if found:
            return found
        if is_macos():
            result = subprocess.run(["brew", "--prefix", self._brew_package()], capture_output=True, text=True)
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
