from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from bench_cli.commands.base import Command

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class StatusCommand(Command):
    name = "status"
    help = "Show bench status summary."

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def run(self) -> None:
        cfg = self.bench.config
        prod = cfg.production

        self._section("Bench")
        self._row("Name", cfg.name)
        self._row("Python", cfg.python_version)
        self._row("Path", str(self.bench.path))

        if not prod.enabled:
            self._row("Mode", "development")
            self._row("Manager", "foreground (Procfile)")
            self._print_processes_dev()
        else:
            self._row("Mode", "production")
            self._print_processes_prod()

        self._section("Sites")
        sites = list(self.bench.sites())
        if sites:
            for site in sites:
                ssl = "  [SSL]" if site.config.ssl else ""
                self._row(site.config.name, f"http{'s' if site.config.ssl else ''}://{site.config.name}{ssl}")
        else:
            print("  (no sites)")

        self._section("Apps")
        apps = list(self.bench.apps())
        if apps:
            for app in apps:
                self._row(app.config.name, app.config.branch)
        else:
            print("  (no apps cloned)")

        if prod.enabled:
            self._section("Nginx")
            nginx_status = self._service_status("nginx")
            self._row("Status", nginx_status)
            self._row("HTTP port", str(cfg.nginx.http_port))
            self._row("HTTPS port", str(cfg.nginx.https_port))

        self._section("Redis")
        redis = cfg.redis
        self._row("Cache port", str(redis.cache_port))
        self._row("Queue port", str(redis.queue_port))

        if cfg.admin.enabled:
            from bench_cli.admin_url import admin_url

            self._section("Admin")
            self._row("URL", admin_url(cfg))
            self._row("Auth", "enabled" if cfg.admin.password else "no password set")

        from bench_cli.platform import is_linux

        if is_linux():
            self._section("Volume (ZFS)")
            self._print_zfs()

        print()

    def _print_processes_dev(self) -> None:
        from bench_cli.managers.process_manager import ProcessManager
        mgr = ProcessManager(self.bench)
        running = mgr.is_running()
        self._row("Processes", _ok("running") if running else _dim("stopped"))

    def _print_processes_prod(self) -> None:
        from bench_cli.managers.process_manager import ProcessManagerFactory
        cfg = self.bench.config
        mgr = ProcessManagerFactory.create(self.bench)
        configured = mgr.is_configured()
        self._row("Configured manager", cfg.production.process_manager)
        if not configured:
            self._row("Installed manager", _warn("missing"))
            self._row("State", _warn("incomplete deployment"))
            print(f"\n  Repair:\n    bench -b {cfg.name} setup production")
            return
        self._row("Installed manager", _ok(cfg.production.process_manager))
        workload = mgr.is_running()
        admin = mgr.admin_is_running()
        self._row("Workload", _ok("running") if workload else _dim("stopped"))
        self._row("Admin", _ok("running") if admin else _dim("stopped"))

    def _print_zfs(self) -> None:
        vol = self.bench.config.volume
        self._row("Pool", vol.pool)
        self._row("Backing", vol.device if vol.backing == "device" else vol.image_path if vol.backing == "image" else "auto (resolved at init)")

        if not shutil.which("zfs"):
            self._row("ZFS data", _warn("zfs binary not found"))
            return

        for label, dataset in [
            ("Benches dataset", f"{vol.pool}/benches"),
            ("MariaDB dataset", f"{vol.pool}/mariadb"),
        ]:
            result = subprocess.run(
                ["zfs", "list", "-Hp", "-o", "used,available,quota,reservation", dataset],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                used, avail, quota, resv = result.stdout.strip().split("\t")
                self._row(label, f"used {_fmt_bytes(int(used))}  avail {_fmt_bytes(int(avail))}  quota {_fmt_bytes(int(quota))}  resv {_fmt_bytes(int(resv))}")
            else:
                self._row(label, _warn("not found"))

    def _service_status(self, service: str) -> str:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True,
            text=True,
        )
        active = result.stdout.strip() == "active"
        return _ok("active") if active else _dim(result.stdout.strip() or "inactive")

    def _section(self, title: str) -> None:
        print(f"\n\033[1m{title}\033[0m")
        print("  " + "─" * (len(title) + 2))

    def _row(self, label: str, value: str) -> None:
        print(f"  {label:<18} {value}")


def _ok(text: str) -> str:
    return f"\033[32m{text}\033[0m"


def _warn(text: str) -> str:
    return f"\033[33m{text}\033[0m"


def _dim(text: str) -> str:
    return f"\033[90m{text}\033[0m"


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n //= 1024
    return f"{n}P"
