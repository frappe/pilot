from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config.common import CommonConfig
from pilot.core.mariadb_memory import calculate_mariadb_memory
from pilot.exceptions import BenchError, DatabaseError
from pilot.internal.atomic_file import atomic_write_private_text
from pilot.utils import benches_dir, cli_root, run_command

if TYPE_CHECKING:
    from pilot.managers.database.mariadb import MariaDBManager


MEMORY_LIMIT_NAMES = ("MemoryHigh", "MemoryMax")
_SIZED_OPTIONS = {
    "innodb-buffer-pool-size",
    "innodb-buffer-pool-size-max",
    "innodb-buffer-pool-size-auto-min",
    "innodb-log-file-size",
    "key-buffer-size",
    "max-connections",
}


class MariaDBMemory:
    def __init__(self, manager: MariaDBManager) -> None:
        self.manager = manager

    @property
    def state_path(self) -> Path:
        return self.manager.config_dir / "memory-sizing.json"

    @property
    def limits_path(self) -> Path:
        return self.manager.unit_path.with_name(self.manager._UNIT_NAME + ".d") / "50-pilot-memory.conf"

    @property
    def boot_id(self) -> str:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    def record_host(self) -> None:
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
        manager = self.manager
        manager._require_linux_managed_server()
        with manager.database_action_lock():
            state = json.loads(self.state_path.read_text()) if self.state_path.exists() else None
            if state is not None and state["boot_id"] == self.boot_id:
                return
            if state is None or state["total_memory_mb"] != manager._total_memory_mb():
                manager.ensure_managed_config()
                options = manager._read_managed_options()
                options = {name: value for name, value in options.items() if name not in _SIZED_OPTIONS}
                atomic_write_private_text(manager.managed_cnf_path, manager._render_managed_options(options))
                sizing = manager._write_config()
                manager._install_unit(sizing, preserve_swap_limit=True)
                self.write_limits(sizing.memory_high_mb, sizing.memory_max_mb)
                self.store_overrides(None, None)
                run_command(manager._systemctl("daemon-reload"), env=manager._systemctl_env())
            else:
                self.restore_overrides()
            self.record_host()

    def restore_overrides(self) -> None:
        config = CommonConfig.read(benches_dir()).mariadb
        high, maximum = config.memory_high_mb, config.memory_max_mb
        if high is None and maximum is None:
            return
        if type(high) is not int or type(maximum) is not int or not 0 < high < maximum:
            raise DatabaseError("Stored MariaDB memory limits must satisfy 0 < MemoryHigh < MemoryMax.")
        self.write_limits(high, maximum)
        manager = self.manager
        run_command(manager._systemctl("daemon-reload"), env=manager._systemctl_env(), timeout=30)

    def install_startup_hook(self) -> None:
        path = self.manager.user_unit_dir / "pilot-mariadb-sizing.service"
        command = shlex.quote(sys.executable).replace("%", "%%")
        atomic_write_private_text(
            path,
            (
                "[Unit]\nDescription=Size MariaDB after host memory changes\n"
                "Before=pilot-mariadb.service\n\n[Service]\nType=oneshot\nRemainAfterExit=yes\n"
                f"WorkingDirectory={str(cli_root()).replace('%', '%%')}\n"
                f"ExecStart={command} -m pilot.internal.mariadb_startup\n"
            ),
        )

    def install_existing(self) -> None:
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

    def host_memory(self) -> dict[str, int]:
        values = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        return {name: int(values[name].split()[0]) // 1024 for name in ("MemTotal", "MemAvailable")}

    def get_limits(self) -> dict[str, int | None]:
        return {name: self.manager._unit_memory_mb(name) for name in MEMORY_LIMIT_NAMES}

    def validate(self, name: str, value: object) -> dict[str, int]:
        if name not in MEMORY_LIMIT_NAMES or type(value) is not int or value <= 0:
            raise ValueError("Memory limits must be positive whole numbers of MiB.")
        limits = self.get_limits()
        previous_high = limits["MemoryHigh"]
        limits[name] = value
        high, maximum = limits["MemoryHigh"], limits["MemoryMax"]
        if high is None or maximum is None or not 0 < high < maximum:
            raise ValueError("MemoryHigh must be positive and lower than MemoryMax; both must be finite.")
        usage = self.manager._unit_memory_mb("MemoryCurrent")
        if usage is None:
            raise DatabaseError("Cannot validate memory limits because MariaDB memory use is unavailable.")
        host = self.host_memory()
        other_usage = max(0, host["MemTotal"] - host["MemAvailable"] - usage)
        if other_usage + maximum > host["MemTotal"] * 9 // 10:
            raise ValueError("MariaDB MemoryMax plus other host memory use cannot exceed 90% of host RAM.")
        if maximum <= usage or ((previous_high is None or high < previous_high) and high <= usage):
            raise ValueError("MemoryHigh must exceed current MariaDB memory use before lowering limits.")
        if maximum <= self.manager.innodb_buffer_pool_size_mb():
            raise ValueError("MemoryMax must exceed the configured InnoDB Buffer Pool size.")
        return {"MemoryHigh": high, "MemoryMax": maximum}

    def write_limits(self, high: int, maximum: int) -> None:
        self.limits_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_private_text(self.limits_path, f"[Service]\nMemoryHigh={high}M\nMemoryMax={maximum}M\n")

    def store_overrides(self, high: int | None, maximum: int | None) -> None:
        with CommonConfig.open(benches_dir()) as config:
            config.mariadb.memory_high_mb = high
            config.mariadb.memory_max_mb = maximum

    def set(self, name: str, value: object) -> None:
        manager = self.manager
        manager._require_linux_managed_server()
        with manager.database_action_lock():
            limits = self.validate(name, value)
            previous = self.get_limits()
            config = CommonConfig.read(benches_dir()).mariadb
            previous_content = self.limits_path.read_text() if self.limits_path.exists() else "[Service]\n"
            self.write_limits(limits["MemoryHigh"], limits["MemoryMax"])
            try:
                self.store_overrides(limits["MemoryHigh"], limits["MemoryMax"])
                self.apply_limits(limits)
                manager._wait_until_healthy()
            except (BenchError, OSError) as error:
                atomic_write_private_text(self.limits_path, previous_content)
                try:
                    self.store_overrides(config.memory_high_mb, config.memory_max_mb)
                    self.apply_limits(previous)
                except (BenchError, OSError) as rollback_error:
                    raise DatabaseError(
                        "Memory update and rollback failed. Review MariaDB service limits."
                    ) from rollback_error
                raise DatabaseError("Memory update failed; previous limits were restored.") from error

    def apply_limits(self, limits: dict[str, int | None]) -> None:
        manager = self.manager
        run_command(manager._systemctl("daemon-reload"), env=manager._systemctl_env(), timeout=30)
        properties = [
            f"{key}={amount}M" if amount is not None else f"{key}=infinity" for key, amount in limits.items()
        ]
        run_command(
            manager._systemctl("set-property", "--runtime", manager._UNIT_NAME, *properties),
            env=manager._systemctl_env(),
            timeout=30,
        )
        if self.get_limits() != limits:
            raise DatabaseError("MariaDB memory limits did not apply.")

    def get_variables(self, edit_reason: str) -> list[dict]:
        limits = self.get_limits()
        sizing = calculate_mariadb_memory(self.manager._total_memory_mb())
        recommended = {"MemoryHigh": sizing.memory_high_mb, "MemoryMax": sizing.memory_max_mb}
        variables = []
        for name, value in limits.items():
            editor = {
                "action": "configuration",
                "value": value,
                "value_type": "integer",
                "unit": "MiB",
                "min": 1,
                "max": None,
                "step": 1,
                "recommended": recommended[name],
                "dynamic_max": None,
                "requires_restart": False,
            }
            variables.append(
                {
                    "name": name,
                    "label": name,
                    "section": "Memory",
                    "description": "Host-wide MariaDB service limit.",
                    "value": value,
                    "value_type": "integer",
                    "unit": "MiB",
                    "dynamic": True,
                    "requires_restart": False,
                    "supported": True,
                    "editable": not edit_reason,
                    "edit": None if edit_reason else editor,
                    "reason": edit_reason,
                    "min": 1,
                    "max": None,
                    "step": 1,
                }
            )
        return variables
