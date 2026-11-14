from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config.common import CommonConfig
from pilot.core.mariadb_memory import calculate_mariadb_memory
from pilot.exceptions import DatabaseError
from pilot.internal.atomic_file import atomic_write_private_text
from pilot.internal.command import run_command

if TYPE_CHECKING:
    from pilot.managers.database.mariadb import MariaDBManager


MEMORY_LIMIT_NAMES = ("MemoryHigh", "MemoryMax")
MEMORY_LIMIT_READ_ONLY_REASON = "Pilot computes this limit from host RAM and the database memory split."


class MariaDBMemory:
    sized_options = frozenset(
        {
            "innodb-buffer-pool-size",
            "innodb-buffer-pool-size-max",
            "innodb-buffer-pool-size-auto-min",
            "innodb-log-file-size",
            "key-buffer-size",
            "aria-pagecache-buffer-size",
            "max-connections",
        }
    )

    def __init__(self, manager: MariaDBManager) -> None:
        self.manager = manager

    @property
    def state_path(self) -> Path:
        """The file with the recorded host memory."""
        return self.manager.config_dir / "memory-sizing.json"

    @property
    def limits_path(self) -> Path:
        """The systemd drop-in with the memory limits."""
        return self.manager.unit_path.with_name(self.manager._UNIT_NAME + ".d") / "50-pilot-memory.conf"

    @property
    def boot_id(self) -> str:
        """The current boot ID."""
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    def record_host(self) -> None:
        """Record the host memory and boot ID."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_private_text(
            self.state_path,
            json.dumps(
                {
                    "boot_id": self.boot_id,
                    "total_memory_mb": self.manager._total_memory_mb(),
                }
            ),
        )

    def prepare_boot(self) -> None:
        """Resize the server for this boot when the host memory changed."""
        from pilot.core.server import Server

        manager = self.manager
        manager._require_linux_managed_server()
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text())
            if state["boot_id"] == self.boot_id:
                return
        with manager.database_action_lock():
            state = json.loads(self.state_path.read_text()) if self.state_path.exists() else None
            if state is not None and state["boot_id"] == self.boot_id:
                return
            if state is None or state["total_memory_mb"] != manager._total_memory_mb():
                manager.ensure_managed_config()
                options = manager._read_managed_options()
                options = {name: value for name, value in options.items() if name not in self.sized_options}
                atomic_write_private_text(manager.managed_cnf_path, manager._render_managed_options(options))
                sizing = manager._write_config()
                manager._install_unit(sizing, preserve_swap_limit=True, reload_manager=False)
                manager._set_runtime_memory_limits(sizing)
            else:
                sizing = calculate_mariadb_memory(manager._total_memory_mb(), manager.config.memory_share)
                manager._install_unit(sizing, preserve_swap_limit=True, reload_manager=False)
                manager._set_runtime_memory_limits(sizing)
            with CommonConfig.open(Server().benches_dir) as config:
                config.mariadb.memory_high_mb = None
                config.mariadb.memory_max_mb = None
            self.record_host()

    def install_startup_hook(self) -> None:
        """Install the unit that resizes the server at boot."""
        from pilot.core.server import Server

        path = self.manager.user_unit_dir / "pilot-mariadb-sizing.service"
        command = shlex.quote(sys.executable).replace("%", "%%")
        atomic_write_private_text(
            path,
            (
                "[Unit]\nDescription=Size MariaDB after host memory changes\n"
                "Before=pilot-mariadb.service\n\n[Service]\nType=oneshot\nRemainAfterExit=yes\n"
                f"WorkingDirectory={str(Server().root).replace('%', '%%')}\n"
                f"Environment=PYTHONPATH={str(Server().root).replace('%', '%%')}\n"
                f"ExecStart={command} -m pilot.internal.mariadb_startup\n"
                "\n[Install]\nWantedBy=pilot-mariadb.service\n"
            ),
        )

    def install_existing(self) -> None:
        """Apply memory sizing to an existing managed server."""
        manager = self.manager
        manager._require_linux_managed_server()
        with manager.database_action_lock():
            if not self.state_path.exists():
                self.record_host()
            unit = manager.unit_path.read_text()
            if not unit.startswith("[Unit]\n"):
                raise DatabaseError("Pilot's MariaDB service unit has no valid Unit section.")
            if "Requires=pilot-mariadb-sizing.service" not in unit:
                unit = unit.replace(
                    "[Unit]\n",
                    "[Unit]\nRequires=pilot-mariadb-sizing.service\nAfter=pilot-mariadb-sizing.service\n",
                    1,
                )
                atomic_write_private_text(manager.unit_path, unit)
            self.install_startup_hook()
            run_command(manager._systemctl("daemon-reload"), env=manager._systemctl_env())
        run_command(
            manager._systemctl("start", "pilot-mariadb-sizing.service"),
            env=manager._systemctl_env(),
            timeout=30,
        )

    def write_limits(self, high: int, maximum: int) -> None:
        """Write the unit memory limits."""
        self.limits_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_private_text(self.limits_path, f"[Service]\nMemoryHigh={high}M\nMemoryMax={maximum}M\n")
