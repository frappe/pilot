from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

from pilot.commands.base import Command
from pilot.utils import write_toml

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class RemoveProductionCommand(Command):
    name = "production"
    help = "Remove a production deployment (keeps logs, certificates, admin domain)."
    group = "remove"

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def run(self) -> None:
        prod = self.bench.config.production
        if not prod.enabled:
            print(f"Bench {self.bench.config.name} is not deployed to production. Nothing to remove.")
            return

        self._remove_process_manager(prod.process_manager)
        self._remove_nginx()
        self._persist_disabled()
        self._print_summary()

    def _remove_process_manager(self, pm: str) -> None:
        if pm == "openrc":
            from pilot.managers.openrc_process_manager import OpenRCProcessManager

            OpenRCProcessManager(self.bench).remove_services()
        elif pm == "systemd":
            from pilot.managers.systemd_process_manager import SystemdProcessManager

            SystemdProcessManager(self.bench).remove_units()
        else:
            from pilot.managers.supervisor_process_manager import SupervisorProcessManager

            SupervisorProcessManager(self.bench).shutdown()

    def _remove_nginx(self) -> None:
        from pilot.managers.nginx_manager import NginxManager

        try:
            NginxManager(self.bench).uninstall_config()
        except Exception as exc:  # nginx not installed / already gone — non-fatal
            print(f"  (nginx cleanup skipped: {exc})")

    def _persist_disabled(self) -> None:
        """Set production.enabled = false but keep admin.domain so the bench can be
        redeployed without reconfiguration."""
        toml_path = self.bench.path / "bench.toml"
        data = tomllib.loads(toml_path.read_text())
        production = data.setdefault("production", {})
        production["enabled"] = False
        production.pop("process_manager", None)
        production.pop("nginx", None)
        write_toml(toml_path, data)

    def _print_summary(self) -> None:
        from pilot.admin_url import admin_url

        name = self.bench.config.name
        # enabled is now false in-memory too, so admin_url() returns the dev URL.
        self.bench.config.production.enabled = False
        self.bench.config.production.process_manager = ""
        print(f"\nProduction deployment removed for {name}.")
        print("\nRun it locally with:")
        print(f"  bench -b {name} start")
        print("\nDevelopment admin:")
        print(f"  {admin_url(self.bench.config)}")
