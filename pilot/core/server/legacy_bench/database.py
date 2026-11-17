from __future__ import annotations

import gzip
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError, CommandError
from pilot.internal.command import run_command

if TYPE_CHECKING:
    from pilot.config.mariadb import MariaDBConfig

LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})
_STDERR_TAIL_BYTES = 4096


@dataclass(frozen=True)
class DatabaseServer:
    """Where a MariaDB server listens: a socket, or a host and port."""

    host: str
    port: int
    socket: str = ""

    @classmethod
    def from_config(cls, mariadb: "MariaDBConfig") -> DatabaseServer:
        return cls(mariadb.host, mariadb.port, mariadb.socket_path)

    @property
    def label(self) -> str:
        return self.socket or f"{self.host}:{self.port}"

    def is_same_as(self, other: DatabaseServer) -> bool:
        """Only a match both sides can prove: an unknown socket is a different server."""
        if self.socket or other.socket:
            return (
                bool(self.socket and other.socket)
                and Path(self.socket).resolve() == Path(other.socket).resolve()
            )
        return self.port == other.port and get_host_key(self.host) == get_host_key(other.host)


def get_host_key(host: str) -> str:
    host = host.strip().lower()
    return "local" if host in LOCAL_HOSTS else host


def get_client_options(server: DatabaseServer, user: str, password: str) -> str:
    def quoted(value: object) -> str:
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = ["[client]", f"user={quoted(user)}", f"password={quoted(password)}"]
    if server.socket:
        lines.append(f"socket={quoted(server.socket)}")
    else:
        lines += [f"host={quoted(server.host)}", f"port={server.port}", "protocol=TCP"]
    return "\n".join(lines) + "\n"


def write_client_options(directory: Path, server: DatabaseServer, user: str, password: str) -> Path:
    """An option file, so the password stays out of argv."""
    options = directory / "client.cnf"
    options.write_text(get_client_options(server, user, password))
    options.chmod(0o600)
    return options


def check_login(server: DatabaseServer, user: str, password: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        options = write_client_options(Path(directory), server, user, password)
        argv = [get_binary("mariadb", "mysql"), f"--defaults-extra-file={options}", "--connect-timeout=5"]
        try:
            run_command([*argv, "-e", "SELECT 1"], timeout=15)
        except CommandError as error:
            raise BenchError(f"Could not log in to {server.label} as '{user}': {error.message}") from error


def dump_database(server: DatabaseServer, user: str, password: str, database: str, target: Path) -> None:
    """Dump `database` gzipped into `target`, in one consistent snapshot."""
    options = write_client_options(target.parent, server, user, password)
    argv = [
        get_binary("mariadb-dump", "mysqldump"),
        f"--defaults-extra-file={options}",
        "--single-transaction",
        "--quick",
        "--lock-tables=false",
        database,
    ]
    with tempfile.TemporaryFile() as stderr, gzip.open(target, "wb") as output:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=stderr)
        assert process.stdout is not None
        shutil.copyfileobj(process.stdout, output)
        if process.wait() != 0:
            stderr.seek(max(0, stderr.seek(0, 2) - _STDERR_TAIL_BYTES))
            error = stderr.read().decode(errors="replace").strip()
            raise BenchError(f"Dumping database '{database}' failed: {error}")


def get_binary(*names: str) -> str:
    binary = next(filter(None, map(shutil.which, names)), None)
    if not binary:
        raise BenchError(f"None of {', '.join(names)} is installed.")
    return binary
