import configparser
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import NoReturn

from pilot.config import MariaDBConfig
from pilot.core.database.mariadb_variables import (
    GENERIC_EDITABLE_MARIADB_VARIABLE_NAMES,
    MARIADB_VARIABLE_NAMES,
    MariaDBValue,
    mariadb_variable_spec,
)
from pilot.core.mariadb_memory import (
    MariaDBMemorySizing,
    MariaDBVariableLimits,
    calculate_mariadb_memory,
    calculate_mariadb_variable_limits,
)
from pilot.exceptions import DatabaseError
from pilot.internal.atomic_file import (
    atomic_write_private_text,
    exclusive_file_lock,
)
from pilot.managers.database.base import UserOwnedDBManager
from pilot.managers.platform import is_macos, which
from pilot.utils import cli_root, run_command

_CLIENT_TIMEOUT = 5
_MANAGED_CONFIG_HEADER = "# Managed by Pilot's database variable editor.\n"
_OPTION_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_MEBIBYTE = 1024 * 1024
_GLOBAL_INTEGER_VARIABLES = {
    "innodb_buffer_pool_size",
    "innodb_buffer_pool_size_max",
    "max_connections",
}


class _ManagedCnfParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


class MariaDBManager(UserOwnedDBManager):
    _UNIT_NAME = "pilot-mariadb.service"
    _DISPLAY_NAME = "MariaDB"
    _SYSTEM_PACKAGE = "mariadb-server"
    _BREW_FORMULA_BASE = "mariadb"
    _DEFAULT_VERSION = "11.8"

    def __init__(self, config: MariaDBConfig) -> None:
        self.config = config

    @property
    def state_dir(self) -> Path:
        """One rootless MariaDB server per host, shared by all its benches."""
        return cli_root() / "databases" / "mariadb"

    @property
    def config_dir(self) -> Path:
        return self.state_dir / "config"

    @property
    def my_cnf_path(self) -> Path:
        return self.config_dir / "my.cnf"

    @property
    def managed_cnf_path(self) -> Path:
        return self.config_dir / "managed.cnf"

    @property
    def action_lock_path(self) -> Path:
        return self.config_dir / "database-action"

    @property
    def data_dir(self) -> Path:
        return self.state_dir / "data"

    @property
    def run_dir(self) -> Path:
        return self.state_dir / "run"

    @property
    def pid_file(self) -> Path:
        return self.run_dir / "mysqld.pid"

    @property
    def socket_path(self) -> str:
        if self.config.socket_path:
            return self.config.socket_path
        return str(self.run_dir / "mysqld.sock")

    def is_installed(self) -> bool:
        # which() searches sbin too; mysqld/mariadbd live in /usr/sbin.
        return bool(which("mysqld") or which("mariadbd"))

    def is_provisioned(self) -> bool:
        if is_macos():
            return self.is_running() and not self.is_unsecured()
        return super().is_provisioned() and self.my_cnf_path.exists()

    def _provision_macos(self):
        if not self.is_running():
            self.start()
        self._wait_until_reachable()
        self.secure_installation()

    def provision(self) -> None:
        """Install, start and secure the shared MariaDB server."""
        self.install()
        if is_macos():
            return self._provision_macos()

        if not self.is_provisioned():
            sizing = self._write_config()
            self._initialize_data_dir()
            self._install_unit(sizing)
        self.enable_at_boot()

        self._wait_until_reachable()
        self.secure_installation()
        return None

    def _initialize_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        run_command(
            [
                "mariadb-install-db",
                f"--defaults-file={self.my_cnf_path}",
                f"--datadir={self.data_dir}",
                "--skip-test-db",
            ]
        )

    def _write_config(self) -> MariaDBMemorySizing:
        """Write every server setting into our own my.cnf so mariadbd, launched
        with --defaults-file, never falls back to reading /etc/mysql/my.cnf."""
        total_memory_mb = self._total_memory_mb()
        sizing = calculate_mariadb_memory(total_memory_mb)
        limits = calculate_mariadb_variable_limits(total_memory_mb)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_managed_cnf()
        content = (
            "# Managed by Pilot.\n"
            "[mysqld]\n"
            f"datadir = {self.data_dir}\n"
            f"socket = {self.socket_path}\n"
            f"port = {self.config.port}\n"
            f"pid-file = {self.pid_file}\n"
            "bind-address = 127.0.0.1\n"
            "\n"
            "default-storage-engine = InnoDB\n"
            "character-set-server = utf8mb4\n"
            "collation-server = utf8mb4_unicode_ci\n"
            "character-set-client-handshake = OFF\n"
            "local-infile = OFF\n"
            "max-allowed-packet = 512M\n"
            "\n"
            f"innodb-buffer-pool-size = {sizing.innodb_buffer_pool_mb}M\n"
            f"innodb-buffer-pool-size-max = {limits.innodb_buffer_pool_max_mb}M\n"
            f"innodb-buffer-pool-size-auto-min = {limits.innodb_buffer_pool_min_mb}M\n"
            f"innodb-log-file-size = {sizing.innodb_log_file_mb}M\n"
            "innodb-flush-log-at-trx-commit = 1\n"
            "innodb-print-all-deadlocks = ON\n"
            "innodb-stats-persistent-sample-pages = 256\n"
            "innodb-strict-mode = ON\n"
            "innodb-snapshot-isolation = OFF\n"
            f"key-buffer-size = {sizing.key_buffer_mb}M\n"
            f"max-connections = {sizing.max_connections}\n"
            "slave-connections-needed-for-purge = 0\n"
            "tmp-table-size = 32M\n"
            "max-heap-table-size = 32M\n"
            "\n"
            "[client]\n"
            f"socket = {self.socket_path}\n"
            f"port = {self.config.port}\n"
            "default-character-set = utf8mb4\n"
            "\n"
            "[mariadb-dump]\n"
            "max-allowed-packet = 512M\n"
            "\n"
            f"{self._managed_include_line}\n"
        )
        atomic_write_private_text(self.my_cnf_path, content)
        return sizing

    @property
    def _managed_include_line(self) -> str:
        return f"!include {self.managed_cnf_path}"

    def ensure_managed_config(self) -> None:
        """Create the variable-editor option file and include it from my.cnf.

        Existing Pilot installations predate managed.cnf, so the first guarded
        configuration action migrates their generated option file in place.
        """
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_managed_cnf()
        if not self.my_cnf_path.is_file():
            raise DatabaseError("Pilot's MariaDB option file is missing.")

        content = self.my_cnf_path.read_text(encoding="utf-8")
        if any(line.strip() == self._managed_include_line for line in content.splitlines()):
            return
        migrated = f"{content.rstrip()}\n\n{self._managed_include_line}\n"
        atomic_write_private_text(self.my_cnf_path, migrated)

    def _ensure_managed_cnf(self) -> None:
        if self.managed_cnf_path.exists():
            return
        atomic_write_private_text(self.managed_cnf_path, self._render_managed_options({}))

    @staticmethod
    def _render_managed_options(options: dict[str, str]) -> str:
        lines = [_MANAGED_CONFIG_HEADER.rstrip(), "[mysqld]"]
        lines.extend(f"{name} = {options[name]}" for name in sorted(options))
        return "\n".join(lines) + "\n"

    def _read_managed_options(self) -> dict[str, str]:
        parser = _ManagedCnfParser(
            interpolation=None,
            strict=True,
            delimiters=("=",),
            comment_prefixes=("#", ";"),
        )
        try:
            parser.read_string(self.managed_cnf_path.read_text(encoding="utf-8"))
        except (OSError, configparser.Error) as exc:
            raise DatabaseError("Pilot's managed MariaDB configuration is invalid.") from exc
        if parser.sections() != ["mysqld"]:
            raise DatabaseError("Pilot's managed MariaDB configuration must contain only [mysqld].")

        options: dict[str, str] = {}
        for raw_name, raw_value in parser.items("mysqld"):
            name = raw_name.strip().lower().replace("_", "-")
            value = raw_value.strip()
            if not _OPTION_NAME.fullmatch(name) or "\n" in value or "\r" in value:
                raise DatabaseError("Pilot's managed MariaDB configuration contains an invalid option.")
            if name in options:
                raise DatabaseError(f"Pilot's managed MariaDB configuration repeats '{name}'.")
            options[name] = value
        return options

    def _write_managed_option(self, name: str, value: str) -> None:
        self._write_managed_options({name: value})

    def _write_managed_options(self, changes: dict[str, str]) -> None:
        options = self._read_managed_options()
        options.update(changes)
        atomic_write_private_text(self.managed_cnf_path, self._render_managed_options(options))

    @contextmanager
    def database_action_lock(self):
        """Serialize host-wide database mutations, including across benches."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        stack = ExitStack()
        try:
            stack.enter_context(exclusive_file_lock(self.action_lock_path, blocking=False))
        except BlockingIOError as exc:
            raise DatabaseError("Another database action is already running on this server.") from exc
        with stack:
            yield

    def restart_managed_server(self) -> None:
        self._require_managed_server()
        with self.database_action_lock():
            self._restart_and_wait_healthy()

    def performance_schema_enabled(self) -> bool:
        connection = None
        try:
            connection = self.connect()
            with connection.cursor() as cursor:
                cursor.execute("SELECT @@GLOBAL.performance_schema")
                row = cursor.fetchone()
        except Exception as exc:
            raise DatabaseError("Could not read the Performance Schema state.") from exc
        finally:
            if connection is not None:
                connection.close()
        if not row:
            raise DatabaseError("Could not read the Performance Schema state.")
        return bool(row[0])

    def set_performance_schema(
        self,
        enabled: bool,
        restart_executor: Callable[[Callable[[], None]], None] | None = None,
    ) -> bool:
        """Persist, restart, verify, and roll back a Performance Schema change."""
        if type(enabled) is not bool:
            raise DatabaseError("Performance Schema must be either enabled or disabled.")
        if is_macos():
            raise DatabaseError("Pilot-managed MariaDB configuration actions require Linux.")
        self._require_managed_server()

        with self.database_action_lock():
            if not self.is_healthy():
                raise DatabaseError("MariaDB is not reachable with Pilot's admin credentials.")
            previous_enabled = self.performance_schema_enabled()
            if previous_enabled == enabled:
                return False

            self.ensure_managed_config()
            previous_content = self.managed_cnf_path.read_text(encoding="utf-8")
            self._write_managed_option("performance-schema", "ON" if enabled else "OFF")

            try:
                if restart_executor is None:
                    self._restart_and_wait_healthy()
                else:
                    restart_executor(self._restart_and_wait_healthy)
                self._verify_performance_schema(enabled, "apply the requested")
            except Exception as apply_error:
                self._rollback_performance_schema(
                    previous_content,
                    previous_enabled,
                    apply_error,
                )
            return True

    def variable_limits(self) -> MariaDBVariableLimits:
        return calculate_mariadb_variable_limits(self._total_memory_mb())

    def innodb_buffer_pool_size_mb(self) -> int:
        return self._read_global_integer("innodb_buffer_pool_size") // _MEBIBYTE

    def innodb_buffer_pool_size_max_mb(self) -> int:
        return self._read_global_integer("innodb_buffer_pool_size_max") // _MEBIBYTE

    def max_connections(self) -> int:
        return self._read_global_integer("max_connections")

    def set_innodb_buffer_pool_size(self, size_mb: int) -> bool:
        """Apply a live resize when possible, otherwise restart for a larger 11.8 ceiling."""
        self._require_integer(size_mb, "InnoDB Buffer Pool size")
        self._require_linux_managed_server()

        with self.database_action_lock():
            self._require_healthy_server()
            limits = self.variable_limits()
            self._validate_innodb_buffer_pool_size(size_mb, limits)
            previous_size_mb = self.innodb_buffer_pool_size_mb()
            dynamic_max_mb = self.innodb_buffer_pool_size_max_mb()

            self.ensure_managed_config()
            previous_content = self.managed_cnf_path.read_text(encoding="utf-8")
            changes = {
                "innodb-buffer-pool-size": f"{size_mb}M",
                "innodb-buffer-pool-size-auto-min": f"{limits.innodb_buffer_pool_min_mb}M",
                "innodb-buffer-pool-size-max": f"{limits.innodb_buffer_pool_max_mb}M",
            }
            current_options = self._read_managed_options()
            if previous_size_mb == size_mb and all(
                current_options.get(name) == value for name, value in changes.items()
            ):
                return False

            restart_required = size_mb > dynamic_max_mb
            self._write_managed_options(changes)
            try:
                if restart_required:
                    self._restart_and_wait_healthy()
                elif previous_size_mb != size_mb:
                    self._set_global_integer("innodb_buffer_pool_size", size_mb * _MEBIBYTE)
                self._verify_global_integer(
                    "innodb_buffer_pool_size",
                    size_mb * _MEBIBYTE,
                    "InnoDB Buffer Pool size",
                )
                if self.innodb_buffer_pool_size_max_mb() < size_mb:
                    raise DatabaseError(
                        "MariaDB's InnoDB Buffer Pool ceiling is lower than the requested size."
                    )
            except Exception as apply_error:
                self._rollback_integer_variable(
                    previous_content,
                    "innodb_buffer_pool_size",
                    previous_size_mb * _MEBIBYTE,
                    "InnoDB Buffer Pool size",
                    restart_required,
                    apply_error,
                )
            return True

    def set_max_connections(self, max_connections: int) -> bool:
        """Persist and apply MariaDB's dynamic connection ceiling."""
        self._require_integer(max_connections, "Max DB connections")
        self._require_linux_managed_server()

        with self.database_action_lock():
            self._require_healthy_server()
            limits = self.variable_limits()
            self._validate_max_connections(max_connections, limits)
            previous_max_connections = self.max_connections()

            self.ensure_managed_config()
            previous_content = self.managed_cnf_path.read_text(encoding="utf-8")
            current_options = self._read_managed_options()
            requested_value = str(max_connections)
            if (
                previous_max_connections == max_connections
                and current_options.get("max-connections") == requested_value
            ):
                return False

            self._write_managed_option("max-connections", requested_value)
            try:
                if previous_max_connections != max_connections:
                    self._set_global_integer("max_connections", max_connections)
                self._verify_global_integer(
                    "max_connections",
                    max_connections,
                    "Max DB connections",
                )
            except Exception as apply_error:
                self._rollback_integer_variable(
                    previous_content,
                    "max_connections",
                    previous_max_connections,
                    "Max DB connections",
                    False,
                    apply_error,
                )
            return True

    def global_variable_values(self, variable_names: Iterable[str]) -> dict[str, str]:
        """Read known variables in one query without interpolating their names."""
        names = tuple(dict.fromkeys(variable_names))
        unsupported = set(names) - MARIADB_VARIABLE_NAMES
        if unsupported:
            name = sorted(unsupported)[0]
            raise DatabaseError(f"Pilot cannot read MariaDB variable '{name}'.")
        if not names:
            return {}

        placeholders = ", ".join(["%s"] * len(names))
        connection = None
        try:
            connection = self.connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT VARIABLE_NAME, VARIABLE_VALUE "
                    "FROM information_schema.GLOBAL_VARIABLES "
                    f"WHERE VARIABLE_NAME IN ({placeholders})",
                    names,
                )
                rows = cursor.fetchall()
        except Exception as exc:
            raise DatabaseError("Could not read MariaDB configuration variables.") from exc
        finally:
            if connection is not None:
                connection.close()
        return {str(name).lower(): str(value) for name, value in rows}

    def set_configuration_variable(self, name: str, value: MariaDBValue) -> bool:
        """Persist, apply, verify, and roll back one safe dynamic variable."""
        spec = self._configuration_variable_spec(name)
        try:
            requested = spec.validate_input(value)
        except ValueError as exc:
            raise DatabaseError(str(exc)) from exc
        if not spec.dynamic:
            raise DatabaseError(f"MariaDB variable '{name}' cannot be changed while running.")
        self._require_linux_managed_server()

        with self.database_action_lock():
            self._require_healthy_server()
            previous = self._read_configuration_variable(name)
            if previous == requested:
                return False

            self.ensure_managed_config()
            previous_content = self.managed_cnf_path.read_text(encoding="utf-8")
            self._write_managed_option(spec.option_name, spec.option_value(requested))
            try:
                self._set_configuration_variable(name, requested)
                self._verify_configuration_variable(name, requested)
            except Exception as apply_error:
                self._rollback_configuration_variable(
                    previous_content,
                    name,
                    previous,
                    apply_error,
                )
            return True

    def _read_configuration_variable(self, name: str) -> MariaDBValue:
        spec = self._configuration_variable_spec(name)
        raw_value = self.global_variable_values((name,)).get(name)
        if raw_value is None:
            raise DatabaseError(f"MariaDB variable '{name}' is not exposed by the installed MariaDB version.")
        try:
            return spec.parse_server_value(raw_value)
        except ValueError as exc:
            raise DatabaseError(str(exc)) from exc

    def _set_configuration_variable(self, name: str, value: MariaDBValue) -> None:
        spec = self._configuration_variable_spec(name)
        try:
            validated = spec.validate_input(value)
        except ValueError as exc:
            raise DatabaseError(str(exc)) from exc
        sql_value = int(validated) if type(validated) is bool else validated
        connection = None
        try:
            connection = self.connect()
            with connection.cursor() as cursor:
                cursor.execute(f"SET GLOBAL {spec.name} = %s", (sql_value,))
        except Exception as exc:
            raise DatabaseError(f"Could not update MariaDB variable '{name}'.") from exc
        finally:
            if connection is not None:
                connection.close()

    def _verify_configuration_variable(self, name: str, expected: MariaDBValue) -> None:
        if self._read_configuration_variable(name) != expected:
            raise DatabaseError(f"MariaDB did not apply the requested value for '{name}'.")

    def _rollback_configuration_variable(
        self,
        previous_content: str,
        name: str,
        previous_value: MariaDBValue,
        apply_error: Exception,
    ) -> NoReturn:
        try:
            atomic_write_private_text(self.managed_cnf_path, previous_content)
            self._set_configuration_variable(name, previous_value)
            self._verify_configuration_variable(name, previous_value)
        except Exception as rollback_error:
            raise DatabaseError(
                f"Could not apply MariaDB variable '{name}', and restoring the previous "
                f"configuration also failed: {rollback_error}"
            ) from apply_error
        raise DatabaseError(
            f"Could not apply MariaDB variable '{name}'. The previous configuration was restored."
        ) from apply_error

    @staticmethod
    def _configuration_variable_spec(name: str):
        if name not in GENERIC_EDITABLE_MARIADB_VARIABLE_NAMES:
            raise DatabaseError(f"Pilot cannot change MariaDB variable '{name}'.")
        return mariadb_variable_spec(name)

    def _read_global_integer(self, variable: str) -> int:
        self._require_supported_integer_variable(variable)
        connection = None
        try:
            connection = self.connect()
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT @@GLOBAL.{variable}")
                row = cursor.fetchone()
        except Exception as exc:
            raise DatabaseError(f"Could not read MariaDB variable '{variable}'.") from exc
        finally:
            if connection is not None:
                connection.close()
        if not row or type(row[0]) is bool or not isinstance(row[0], int):
            raise DatabaseError(f"MariaDB variable '{variable}' did not return an integer.")
        return row[0]

    def _set_global_integer(self, variable: str, value: int) -> None:
        self._require_supported_integer_variable(variable)
        connection = None
        try:
            connection = self.connect()
            with connection.cursor() as cursor:
                cursor.execute(f"SET GLOBAL {variable} = %s", (value,))
        except Exception as exc:
            raise DatabaseError(f"Could not update MariaDB variable '{variable}'.") from exc
        finally:
            if connection is not None:
                connection.close()

    def _verify_global_integer(self, variable: str, expected: int, label: str) -> None:
        if self._read_global_integer(variable) != expected:
            raise DatabaseError(f"MariaDB did not apply the requested {label}.")

    def _rollback_integer_variable(
        self,
        previous_content: str,
        variable: str,
        previous_value: int,
        label: str,
        restart_required: bool,
        apply_error: Exception,
    ) -> NoReturn:
        try:
            atomic_write_private_text(self.managed_cnf_path, previous_content)
            if restart_required:
                self._restart_and_wait_healthy()
            if self._read_global_integer(variable) != previous_value:
                self._set_global_integer(variable, previous_value)
            self._verify_global_integer(variable, previous_value, f"previous {label}")
        except Exception as rollback_error:
            raise DatabaseError(
                f"Could not apply the {label}, and restoring the previous configuration "
                f"also failed: {rollback_error}"
            ) from apply_error
        raise DatabaseError(
            f"Could not apply the {label}. The previous configuration was restored."
        ) from apply_error

    @staticmethod
    def _validate_innodb_buffer_pool_size(
        size_mb: int,
        limits: MariaDBVariableLimits,
    ) -> None:
        if size_mb < limits.innodb_buffer_pool_min_mb:
            raise DatabaseError(
                f"InnoDB Buffer Pool size cannot be less than {limits.innodb_buffer_pool_min_mb} MB."
            )
        if size_mb > limits.innodb_buffer_pool_max_mb:
            raise DatabaseError(
                "InnoDB Buffer Pool size cannot be greater than "
                f"{limits.innodb_buffer_pool_max_mb} MB on this server."
            )

    @staticmethod
    def _validate_max_connections(
        max_connections: int,
        limits: MariaDBVariableLimits,
    ) -> None:
        if max_connections < limits.max_connections_min:
            raise DatabaseError(f"Max DB connections must be at least {limits.max_connections_min}.")
        if max_connections > limits.max_connections_max:
            raise DatabaseError(
                f"Max DB connections cannot be greater than {limits.max_connections_max} on this server."
            )

    @staticmethod
    def _require_integer(value: int, label: str) -> None:
        if type(value) is not int:
            raise DatabaseError(f"{label} must be a whole number.")

    @staticmethod
    def _require_supported_integer_variable(variable: str) -> None:
        if variable not in _GLOBAL_INTEGER_VARIABLES:
            raise DatabaseError(f"Pilot cannot change MariaDB variable '{variable}'.")

    def _require_linux_managed_server(self) -> None:
        if is_macos():
            raise DatabaseError("Pilot-managed MariaDB configuration actions require Linux.")
        self._require_managed_server()

    def _require_healthy_server(self) -> None:
        if not self.is_healthy():
            raise DatabaseError("MariaDB is not reachable with Pilot's admin credentials.")

    def _verify_performance_schema(self, expected: bool, operation: str) -> None:
        if self.performance_schema_enabled() != expected:
            raise DatabaseError(f"MariaDB did not {operation} Performance Schema state.")

    def _rollback_performance_schema(
        self,
        previous_content: str,
        previous_enabled: bool,
        apply_error: Exception,
    ) -> NoReturn:
        try:
            atomic_write_private_text(self.managed_cnf_path, previous_content)
            self._restart_and_wait_healthy()
            self._verify_performance_schema(previous_enabled, "restore the previous")
        except Exception as rollback_error:
            raise DatabaseError(
                "Could not apply the Performance Schema change, and restoring the previous "
                f"configuration also failed: {rollback_error}"
            ) from apply_error
        raise DatabaseError(
            "Could not apply the Performance Schema change. The previous configuration was restored."
        ) from apply_error

    def _require_managed_server(self) -> None:
        if self.config.existing:
            raise DatabaseError("Pilot cannot change an external MariaDB server.")
        if not self.is_installed():
            raise DatabaseError("MariaDB is not installed on this server.")
        if not self.is_provisioned():
            raise DatabaseError("Pilot's MariaDB server has not been provisioned.")

    def _restart_and_wait_healthy(self) -> None:
        if not is_macos():
            self._reset_failed_state()
        self.restart()
        self._wait_until_healthy()

    def _wait_until_healthy(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_healthy():
                return
            time.sleep(0.5)
        raise DatabaseError(f"MariaDB did not become healthy within {timeout:.0f}s.")

    def is_healthy(self) -> bool:
        try:
            connection = self.connect()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    return cursor.fetchone() is not None
            finally:
                connection.close()
        except Exception:
            return False

    def _total_memory_mb(self) -> int:
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError) as exc:
            raise DatabaseError("Could not detect total system memory for MariaDB sizing.") from exc
        total_memory_mb = pages * page_size // (1024 * 1024)
        if total_memory_mb <= 0:
            raise DatabaseError("Could not detect total system memory for MariaDB sizing.")
        return total_memory_mb

    def _install_unit(self, sizing: MariaDBMemorySizing) -> None:
        mariadbd = which("mariadbd") or which("mysqld") or "/usr/sbin/mariadbd"
        content = (
            "[Unit]\n"
            "Description=MariaDB (pilot, user-owned)\n\n"
            "[Service]\n"
            "Type=simple\n"
            # --defaults-file must be the first argument; it makes mariadbd
            # skip every system default file instead of layering over them.
            f"ExecStart={mariadbd} --defaults-file={self.my_cnf_path}\n"
            "LimitNOFILE=65535\n"
            f"MemoryHigh={sizing.memory_high_mb}M\n"
            f"MemoryMax={sizing.memory_max_mb}M\n"
            "MemorySwapMax=100M\n"
            "Restart=on-failure\n\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )
        unit_dir = self.user_unit_dir
        unit_dir.mkdir(parents=True, exist_ok=True)
        self.unit_path.write_text(content)
        run_command(self._systemctl("daemon-reload"), env=self._systemctl_env())

    def is_reachable(self) -> bool:
        if not self.is_running():
            return False
        if is_macos():
            # Homebrew owns the socket location here, not socket_path() (our
            # own state_dir, only ever created for the Linux systemd unit) -
            # is_running() is the only signal we have.
            return True
        return Path(self.socket_path).exists()

    def is_unsecured(self) -> bool:
        """True when the admin account is still reachable without a password."""
        cmd = ["mariadb"]
        if not is_macos():
            cmd.append(f"--socket={self.socket_path}")
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

    def has_valid_credentials(self, password: str | None = None) -> bool:
        """Check admin credentials using MYSQL_PWD, never argv."""
        pw = self.config.root_password if password is None else password
        try:
            result = subprocess.run(
                [*self._client_command(), "--skip-column-names", "-e", "SELECT 1"],
                env={**os.environ, "MYSQL_PWD": pw},
                capture_output=True,
                text=True,
                timeout=_CLIENT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def _client_command(self) -> list[str]:
        """Client argv for the admin account, over the socket when there is one."""
        cmd = [
            "mariadb",
            f"--connect-timeout={_CLIENT_TIMEOUT}",
            "-u",
            self.config.admin_user,
            "--batch",
        ]
        socket_path = self._detect_socket()
        if socket_path:
            cmd.append(f"--socket={socket_path}")
        else:
            cmd += ["-h", self.config.host, "-P", str(self.config.port)]
        return cmd

    def run_admin_sql(self, sql: str) -> None:
        """Run statements as the admin account, with the password in MYSQL_PWD."""
        subprocess.run(
            self._client_command(),
            input=sql,
            text=True,
            check=True,
            capture_output=True,
            timeout=_CLIENT_TIMEOUT,
            env={**os.environ, "MYSQL_PWD": self.config.root_password},
        )

    @contextmanager
    def temporary_setup_user(self, db_name: str):
        """A throwaway account holding only what frappe needs to build `db_name`.

        frappe takes its database credential on the command line, where every local
        process can read it, so the long-lived admin password must not go there. The
        host is '%' because the client may reach the server over a socket, over
        loopback, or from another host, and the account lives for one command.
        """
        import secrets

        user = f"pilot_setup_{secrets.token_hex(4)}"
        password = secrets.token_urlsafe(24)
        quoted_user = self._sql_quote(user)
        database = db_name.replace("`", "")
        self.run_admin_sql(
            "\n".join(
                [
                    f"CREATE USER {quoted_user}@'%' IDENTIFIED BY {self._sql_quote(password)};",
                    f"GRANT RELOAD, CREATE USER ON *.* TO {quoted_user}@'%';",
                    f"GRANT ALL PRIVILEGES ON `{database}`.* TO {quoted_user}@'%' WITH GRANT OPTION;",
                    "FLUSH PRIVILEGES;",
                ]
            )
        )
        try:
            yield user, password
        finally:
            self.run_admin_sql(f"DROP USER IF EXISTS {quoted_user}@'%';\nFLUSH PRIVILEGES;")

    def secure_installation(self) -> None:
        """Create/update the admin account and apply basic hardening."""
        if self.has_valid_credentials():
            return
        # admin_user can come from setup wizard input; quote it like a value.
        user = self._sql_quote(self.config.admin_user)
        password = self._sql_quote(self.config.root_password)
        statements = [
            # Covers fresh installs and siblings that secured a different password.
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
            # mariadb-install-db grants this OS user unix_socket admin access.
            cmd.append(f"--socket={self.socket_path}")
        # macOS uses Homebrew's default socket, not our Linux state dir.
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
        """Open an admin connection to the shared MariaDB server."""
        import pymysql

        return pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.admin_user,
            password=self.config.root_password if password is None else password,
            unix_socket=self._detect_socket() or None,
            cursorclass=cursorclass or pymysql.cursors.Cursor,
            connect_timeout=_CLIENT_TIMEOUT,
            read_timeout=_CLIENT_TIMEOUT,
            write_timeout=_CLIENT_TIMEOUT,
        )

    def _detect_socket(self) -> str:
        if self.config.socket_path:
            return self.config.socket_path
        if self.config.existing:
            return ""
        if not is_macos() and Path(self.socket_path).exists():
            return self.socket_path
        return ""
