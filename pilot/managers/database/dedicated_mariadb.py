from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from pilot.core.database.log_file import LogFile
from pilot.core.database.mariadb_variables import EDITABLE_MARIADB_VARIABLE_NAMES
from pilot.core.mariadb_memory import MariaDBMemorySizing
from pilot.exceptions import DatabaseError
from pilot.managers.database.mariadb import MariaDBManager
from pilot.managers.database.memory import MariaDBMemory
from pilot.managers.platform import HostPlatform


class DedicatedMariaDBManager(MariaDBManager):
    """Pilot's MariaDB on a database VM of its own, which the app VM's Pilot manages through the database agent.

    It runs as Pilot's user unit, like the server Pilot runs beside benches, but it has the VM to itself and
    serves clients on the network.
    """

    # The variable editor and the memory quick actions write these. An option such as init-file or plugin-load
    # would run code as this user, so the managed config holds nothing else.
    managed_option_names = (
        frozenset(name.replace("_", "-") for name in EDITABLE_MARIADB_VARIABLE_NAMES)
        | MariaDBMemory.sized_options
        | {"performance-schema"}
    )

    @property
    def memory_share(self) -> float:
        """All of the VM's memory: nothing else runs on it."""
        return 1.0

    @property
    def client_files_dir(self) -> Path:
        """The only directory SQL may read or write files in. The app VM's admin holds FILE, which would
        otherwise reach every file this user can."""
        return self.state_dir / "client-files"

    @property
    def listen_options(self) -> str:
        """The app VM connects over the network. The firewall and the account hosts limit who can, and the
        accounts name its address, so a reverse lookup could only slow down or break a login."""
        return f"bind-address = ::\nskip-name-resolve = ON\nsecure-file-priv = {self.client_files_dir}\n"

    def _write_config(self) -> MariaDBMemorySizing:
        """Write the option file, with the directory `secure-file-priv` names in place."""
        self.client_files_dir.mkdir(parents=True, exist_ok=True)
        return super()._write_config()

    def control(self, action: str) -> None:
        """Start, stop or restart the server, clearing a failed state first."""
        self._reset_failed_state()
        self._control(action)

    def get_server_version(self) -> tuple[int, int]:
        """The major and minor version of the server binary."""
        mariadbd = HostPlatform().get_executable_path("mariadbd") or "/usr/sbin/mariadbd"
        output = subprocess.run([mariadbd, "--version"], capture_output=True, text=True, check=True).stdout
        if not (match := re.search(r"Ver (\d+)\.(\d+)", output)):
            raise DatabaseError("Could not read the MariaDB version.")
        return int(match.group(1)), int(match.group(2))

    @property
    def free_disk_bytes(self) -> int:
        """Free bytes on the file system that holds the data directory."""
        return shutil.disk_usage(self.data_dir).free

    def get_host_metrics(self) -> dict:
        """One system metrics record of this VM, with the disk that holds the data directory."""
        # psutil is in the agent's venv only, and the CLI imports this module.
        from pilot.core.server.monitoring_proc import HostSampler

        return HostSampler(self.data_dir).get_sampled_record()

    def grant_client_admin(self, user: str, password: str, host: str) -> None:
        """Create or reset the admin account the app VM's Pilot logs in with, from its address only."""
        self._run_sql_as_superuser(self.get_admin_account_sql(user, password, host))

    def replace_managed_config(self, content: str) -> None:
        """Replace the variable editor's option file with text from the app VM; raise for any other option."""
        if unknown := sorted(set(self.parse_managed_options(content)) - self.managed_option_names):
            raise DatabaseError(f"Pilot does not manage these MariaDB options: {', '.join(unknown)}")
        self.write_managed_config(content)

    def get_server_file(self, path: str) -> LogFile:
        """A file of this server for the app VM to read: a log or a file in the data directory."""
        resolved = Path(path).resolve()
        logs = {self.error_log_path.resolve(), self.slow_log_path.resolve()}
        if resolved not in logs and not resolved.is_relative_to(self.data_dir.resolve()):
            raise DatabaseError(f"{path} is not a file of this MariaDB server.")
        return LogFile(resolved)
