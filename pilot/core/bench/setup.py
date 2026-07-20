from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from pilot.core.bench.admin_domain import ProductionAdminDomain
from pilot.exceptions import BenchError
from pilot.utils import write_private_text

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class ProductionSetup:
    """Deploys process management, nginx, TLS, admin routing, and monitoring."""

    def __init__(
        self,
        bench: "Bench",
        process_manager: str | None = None,
        admin_domain: str | None = None,
        admin_tls: bool | None = None,
        letsencrypt_email: str | None = None,
        best_effort_tls: bool = False,
    ) -> None:
        self.bench = bench
        self._pm_arg = process_manager
        self._domain_arg = admin_domain
        self._tls_arg = admin_tls
        self._email_arg = letsencrypt_email
        # CLI callers want requested TLS to fail hard. Wizard hand-offs tolerate
        # pending DNS and leave the bench on HTTP until a retry can issue certs.
        self._best_effort_tls = best_effort_tls
        self._admin_domain = ProductionAdminDomain(bench, bench.config.admin.domain)

    def run(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        self._require_linux()
        self._check_sudo_available()
        self._resolve_target()
        self._require_production_inputs()
        self._check_admin_domain()
        self._register_admin_domain()
        try:
            self.bench.config.validate()
            old_pm = self._installed_manager()
            self._write_dns_multitenancy()
            pm = self.bench.config.production.process_manager
            if pm == "systemd":
                self._setup_systemd()
            else:
                self._setup_supervisor()

            # Free the old manager's ports before the new one binds them.
            self._migrate_from(old_pm, on_progress)
            self._start_workload()
            self.bench.setup_nginx(on_progress=on_progress)
            self._setup_letsencrypt_if_needed()

            self._build_admin_for_production()

            self._persist_production_state()
            self._setup_monitoring()
        except BaseException:
            # A later step failed but the new admin route is already live at the
            # provider; release it so a failed setup leaves no dead external route.
            self._rollback_admin_domain()
            raise

        # The switch is committed; free the old hostname at the provider.
        self._release_previous_admin_domain()
        self._report_summary(on_progress)

    def _resolve_target(self) -> None:
        """Apply CLI args in memory. The toml is written last."""
        from pilot.config import VALID_PROCESS_MANAGERS, ProductionConfig

        pm = (
            ProductionConfig._normalize_process_manager(
                self._pm_arg or self.bench.config.production.process_manager
            )
            or "systemd"
        )
        if pm not in VALID_PROCESS_MANAGERS:
            raise BenchError(
                f"Invalid process manager '{pm}'. Must be one of {', '.join(VALID_PROCESS_MANAGERS)}."
            )
        self.bench.config.production.process_manager = pm
        self.bench.config.production.enabled = True
        # Production serves the admin behind its domain, so it must be enabled -
        # otherwise the API answers 503 "Admin is disabled". The wizard sets this
        # too; do it here so pure-CLI deploys are reachable as well.
        self.bench.config.admin.enabled = True
        if self._domain_arg:
            self.bench.config.admin.domain = self._domain_arg
        if self._tls_arg is not None:
            self.bench.config.admin.tls = self._tls_arg
        if self._email_arg:
            self.bench.config.letsencrypt.email = self._email_arg

    def _require_production_inputs(self) -> None:
        """Fail before nginx/cert work on required production inputs."""
        from pilot.managers.letsencrypt import letsencrypt_email_required

        if not self.bench.config.admin.domain:
            raise BenchError(
                "An admin domain is required to deploy to production. "
                "Pass --admin-domain <domain> (e.g. --admin-domain admin.example.com), "
                "or set admin.domain in bench.toml."
            )
        if letsencrypt_email_required(self.bench) and not self.bench.config.letsencrypt.email:
            raise BenchError(
                "A contact email is required with --tls for Let's Encrypt. Pass --letsencrypt-email <email>, or set letsencrypt.email in bench.toml."
            )

    def _installed_manager(self) -> str | None:
        """Return the process manager already deployed on disk, if any."""
        from pilot.managers.processes.supervisor import SupervisorProcessManager
        from pilot.managers.processes.systemd import SystemdProcessManager

        if SystemdProcessManager(self.bench).is_configured():
            return "systemd"
        if SupervisorProcessManager(self.bench).is_configured():
            return "supervisor"
        return None

    def _migrate_from(self, old_pm: str | None, on_progress: Callable[[str], None]) -> None:
        """Tear down the previous manager before the new one binds ports."""
        new_pm = self.bench.config.production.process_manager
        if not old_pm or old_pm == new_pm:
            return
        on_progress(f"Migrating from {old_pm} to {new_pm}: removing old manager resources...")
        if old_pm == "supervisor":
            from pilot.managers.processes.supervisor import SupervisorProcessManager

            SupervisorProcessManager(self.bench).shutdown()
        else:
            from pilot.managers.processes.systemd import SystemdProcessManager

            SystemdProcessManager(self.bench).remove_units()

    def _setup_monitoring(self):
        from pilot.core.server.monitoring import MonitorConfigurator, resolve_monitor_log_path
        from pilot.core.site.uptime_monitoring_config import UptimeMonitorConfigurator

        MonitorConfigurator().install()
        self.bench.config.monitor.log_path = resolve_monitor_log_path(self.bench.config)
        self.bench.config.write(self.bench.path)

        UptimeMonitorConfigurator(self.bench).install()

    def _persist_production_state(self) -> None:
        """Write the production state to bench.toml LAST, so the switcher never
        points users at a half-built deployment."""
        prod = self.bench.config.production
        admin = self.bench.config.admin
        self._persist(
            {
                "production": {"enabled": True, "process_manager": prod.process_manager},
                "admin": {"domain": admin.domain, "tls": admin.tls, "enabled": True},
            }
        )

    def _require_linux(self) -> None:
        from pilot.managers.platform import is_linux

        if not is_linux():
            print(
                "Error: bench setup production only runs on Linux servers.\nOn macOS, use 'bench start' for local development.",
                file=sys.stderr,
            )
            sys.exit(1)

    def _check_sudo_available(self) -> None:
        """Fail early when production setup cannot get root privileges."""
        from pilot.managers.platform import has_passwordless_sudo, is_root, which

        if is_root() or has_passwordless_sudo():
            return
        if which("sudo") is None:
            raise BenchError(
                "sudo is required to deploy to production (nginx, certbot, systemd) but is not installed."
            )
        if not sys.stdin.isatty():
            raise BenchError(
                "Deploying to production needs root (nginx, certbot, systemd) and there's no "
                "terminal to prompt for a sudo password. Run this interactively, or configure "
                "passwordless sudo for this user first."
            )

    def _check_admin_domain(self) -> None:
        self._admin_domain.check()

    def _register_admin_domain(self) -> None:
        self._admin_domain.register()

    def _rollback_admin_domain(self) -> None:
        self._admin_domain.rollback()

    def _release_previous_admin_domain(self) -> None:
        self._admin_domain.release_previous()

    def _persist(self, updates: dict) -> None:
        """Merge ``updates`` into bench.toml in place, preserving all other fields."""
        from pilot.config import BenchConfig

        with BenchConfig.open(self.bench.path, mode="raw") as data:
            for section, values in updates.items():
                data.setdefault(section, {}).update(values)
            # Drop the deprecated production.nginx key - nginx is always on in prod.
            data.get("production", {}).pop("nginx", None)

    def _write_dns_multitenancy(self) -> None:
        common_config_path = self.bench.sites_path / "common_site_config.json"
        existing_data: dict = {}
        if common_config_path.exists():
            existing_data = json.loads(common_config_path.read_text())
        existing_data["dns_multitenant"] = 1
        write_private_text(common_config_path, json.dumps(existing_data, indent=2))

    def _setup_supervisor(self) -> None:
        import subprocess

        from pilot.managers.packages import get_package_manager

        pkg = get_package_manager()
        if not pkg.is_installed("supervisor"):
            pkg.install("supervisor")
            subprocess.run(["sudo", "systemctl", "disable", "--now", "supervisor"], check=False)
        from pilot.managers.processes.supervisor import SupervisorProcessManager

        manager = SupervisorProcessManager(self.bench)
        manager.write_config()
        manager.install_config()
        manager.reload_manager_config()

    def _setup_systemd(self) -> None:
        from pilot.managers.processes.systemd import SystemdProcessManager

        manager = SystemdProcessManager(self.bench)
        manager.write_config()
        manager.install_config()
        manager.reload_manager_config()

    def _start_workload(self) -> None:
        """Start the workload (and admin) so the bench is actually serving once
        setup completes - otherwise sites 502 until a separate `bench start`."""
        from pilot.managers.processes.local import ProcessManager

        ProcessManager.for_bench(self.bench).start()

    def _setup_letsencrypt_if_needed(self) -> None:
        from pilot.managers.letsencrypt import is_letsencrypt_required

        if not is_letsencrypt_required(self.bench):
            return
        try:
            self.bench.setup_letsencrypt()
        except Exception as exc:
            if not self._best_effort_tls:
                raise
            print(
                f"Warning: could not obtain a TLS certificate for {self.bench.config.admin.domain} "
                f"yet ({exc}). Continuing on HTTP - retry once its DNS resolves.",
                file=sys.stderr,
            )

    def _build_admin_for_production(self) -> None:
        from admin.backend.frontend import build_admin_frontend

        build_admin_frontend()

    def _report_summary(self, on_progress: Callable[[str], None]) -> None:
        from pilot.managers.nginx import NginxManager

        nginx_manager = NginxManager(self.bench)
        on_progress("\nProduction setup complete.")
        on_progress("Sites:")
        for site in self.bench.sites():
            if site.config.ssl and nginx_manager.has_cert(site.config):
                on_progress(f"  https://{site.config.name}")
            else:
                http_port = self.bench.config.nginx.http_port
                port_suffix = "" if http_port == 80 else f":{http_port}"
                on_progress(f"  http://{site.config.name}{port_suffix}")
        admin_https = self.bench.config.admin.tls and nginx_manager.has_admin_cert
        scheme = "https" if admin_https else "http"
        on_progress(f"Admin:\n  {scheme}://{self.bench.config.admin.domain}")
