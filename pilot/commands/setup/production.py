from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Optional

from pilot.commands.base import Command
from pilot.exceptions import BenchError
from pilot.utils import host_owner

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class SetupProductionCommand(Command):
    name = "production"
    help = "Deploy a bench to production (process manager + nginx)."
    group = "setup"

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--process-manager",
            choices=["systemd", "supervisord", "openrc"],
            default=None,
            help="Process manager to deploy with (defaults to production.process_manager in bench.toml, or systemd — openrc on Alpine).",
        )
        parser.add_argument(
            "--admin-domain",
            default=None,
            help="Admin domain the deployment is reached at (required: pass it here or set admin.domain in bench.toml).",
        )
        parser.add_argument(
            "--tls",
            dest="admin_tls",
            action="store_true",
            default=None,  # None = leave the bench.toml value untouched; only --tls turns it on
            help="Terminate TLS via Let's Encrypt for the admin and SSL-enabled sites. "
            "Omit to serve plain HTTP (a central proxy may terminate TLS upstream).",
        )
        parser.add_argument(
            "--letsencrypt-email",
            dest="letsencrypt_email",
            default=None,
            help="Contact email for Let's Encrypt (required with --tls unless letsencrypt.email is already set in bench.toml).",
        )

    @classmethod
    def from_args(cls, args, bench):
        return cls(
            bench,
            process_manager=args.process_manager,
            admin_domain=args.admin_domain,
            admin_tls=args.admin_tls,
            letsencrypt_email=args.letsencrypt_email,
        )

    def __init__(
        self,
        bench: "Bench",
        process_manager: Optional[str] = None,
        admin_domain: Optional[str] = None,
        admin_tls: Optional[bool] = None,
        letsencrypt_email: Optional[str] = None,
        best_effort_tls: bool = False,
    ) -> None:
        self.bench = bench
        self._pm_arg = process_manager
        self._domain_arg = admin_domain
        self._tls_arg = admin_tls
        self._email_arg = letsencrypt_email
        # CLI callers want a hard failure when the TLS they explicitly asked for
        # doesn't work. The wizard's automatic hand-off doesn't - a cert that
        # can't issue yet (e.g. DNS still propagating for a domain created
        # moments ago) shouldn't roll back an otherwise-working deployment;
        # the bench just stays reachable over HTTP until a retry succeeds.
        self._best_effort_tls = best_effort_tls
        self._existing_admin_domain = bench.config.admin.domain
        self._registered_admin_domain: Optional[str] = None

    def run(self) -> None:
        self._require_linux()
        self._resolve_target()
        self._require_production_inputs()
        self._check_admin_domain()
        self._register_admin_domain()
        try:
            self.bench.config.validate()
            old_pm = self._installed_manager()
            self._write_dns_multitenancy()
            pm = self.bench.config.production.process_manager
            if pm == "openrc":
                self._setup_openrc()
            elif pm == "systemd":
                self._setup_systemd()
            else:
                self._setup_supervisor()

            # Free the old manager's ports before the new one binds them.
            self._migrate_from(old_pm)
            self._start_workload()
            self._setup_nginx()
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
        self._print_summary()

    def _resolve_target(self) -> None:
        """Apply --process-manager / --admin-domain to the in-memory config so the
        rest of setup operates on the requested target. The toml is written last."""
        from pilot.config.bench_config import BenchConfig
        from pilot.config.production_config import VALID_PROCESS_MANAGERS
        from pilot.platform import is_alpine

        default_pm = "openrc" if is_alpine() else "systemd"
        pm = BenchConfig._normalize_process_manager(self._pm_arg or self.bench.config.production.process_manager) or default_pm
        if pm not in VALID_PROCESS_MANAGERS:
            raise BenchError(f"Invalid process manager '{pm}'. Must be one of {', '.join(VALID_PROCESS_MANAGERS)}.")
        if is_alpine() and pm == "systemd":
            # Alpine has no systemd; proceeding would shell out to systemctl and
            # leave an unmanageable deployment. Steer the operator to OpenRC.
            raise BenchError("systemd is not available on Alpine. Use --process-manager openrc (the native Alpine manager).")
        self.bench.config.production.process_manager = pm
        self.bench.config.production.enabled = True
        # Production serves the admin behind its domain, so it must be enabled —
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
        """Fail early and clearly on inputs the setup wizard no longer collects: an
        admin domain (always) and a Let's Encrypt email (only when --tls would
        actually obtain a cert). Better here than deep inside nginx/cert work, or
        as a silently-skipped cert."""
        from pilot.managers.letsencrypt_manager import letsencrypt_email_required

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

    def _installed_manager(self) -> Optional[str]:
        """Which process manager already has a deployment on disk, if any —
        used to migrate when --process-manager differs."""
        from pilot.platform import is_alpine

        # Alpine has only OpenRC; probing the systemd/supervisor managers there
        # would shell out to CLIs that aren't installed.
        if is_alpine():
            from pilot.managers.process_managers.openrc import OpenRCProcessManager

            return "openrc" if OpenRCProcessManager(self.bench).is_configured() else None

        from pilot.managers.process_managers.supervisor import SupervisorProcessManager
        from pilot.managers.process_managers.systemd import SystemdProcessManager

        if SystemdProcessManager(self.bench).is_configured():
            return "systemd"
        if SupervisorProcessManager(self.bench).is_configured():
            return "supervisor"
        return None

    def _migrate_from(self, old_pm: Optional[str]) -> None:
        """Tear down the previous manager so it releases the workload ports before
        the new manager starts and binds them."""
        new_pm = self.bench.config.production.process_manager
        if not old_pm or old_pm == new_pm:
            return
        print(f"Migrating from {old_pm} to {new_pm}: removing old manager resources...")
        if old_pm == "supervisor":
            from pilot.managers.process_managers.supervisor import SupervisorProcessManager

            SupervisorProcessManager(self.bench).shutdown()
        elif old_pm == "openrc":
            from pilot.managers.process_managers.openrc import OpenRCProcessManager

            OpenRCProcessManager(self.bench).remove_services()
        else:
            from pilot.managers.process_managers.systemd import SystemdProcessManager

            SystemdProcessManager(self.bench).remove_units()

    def _setup_monitoring(self):
        """Install the shared bench-monitor timer unit and persist monitor config to bench.toml."""
        from pilot.config.toml_store import BenchTomlStore
        from pilot.core.monitor import ConfigureMonitor, resolve_monitor_log_path

        ConfigureMonitor().install()
        self.bench.config.monitor.log_path = resolve_monitor_log_path(self.bench.config)
        BenchTomlStore(self.bench.path).write(self.bench.config)

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
        from pilot.platform import is_linux

        if not is_linux():
            print(
                "Error: bench setup production only runs on Linux servers.\nOn macOS, use 'bench start' for local development.",
                file=sys.stderr,
            )
            sys.exit(1)

    def _check_admin_domain(self) -> None:
        """Admin is reached only via its domain in production. Use whatever is in
        bench.toml (validate() enforces it is present); just reject a domain that
        another bench already claims."""
        from pilot.core.domain_controller import DomainRouteProvider
        from pilot.utils import matches_wildcard, normalize_host

        domain = self.bench.config.admin.domain
        if not domain:
            return  # validate() raises the required-in-prod error, naming the bench
        owner = host_owner(self.bench.path, domain)
        if owner:
            raise BenchError(f"Admin domain '{domain}' is already used by bench '{owner}'.")
        target = normalize_host(domain)
        for site in self.bench.sites():
            if normalize_host(site.config.name) == target:
                raise BenchError(
                    f"Admin domain '{domain}' conflicts with this bench's own site '{site.config.name}'. "
                    f"An admin domain must not match a site domain."
                )
        # Enforce the wildcard rule only on a new/changed admin domain.
        if normalize_host(domain) == normalize_host(self._existing_admin_domain):
            return
        patterns = DomainRouteProvider.wildcard_domains()
        if patterns and not matches_wildcard(domain, patterns):
            raise BenchError(f"Admin domain must match one of this bench's wildcard domains: {', '.join(patterns)}.")

    def _register_admin_domain(self) -> None:
        """Provision a new/changed admin domain with the provider before nginx/cert
        work that would be wasted on a failure. _check_admin_domain has already
        enforced the wildcard rule for it. Records the new domain so the run can
        roll it back on failure or release the previous one on success."""
        from pilot.core.domain_controller import DomainRouteProvider
        from pilot.utils import normalize_host

        self._registered_admin_domain = None
        domain = self.bench.config.admin.domain
        if not domain or normalize_host(domain) == normalize_host(self._existing_admin_domain):
            return
        DomainRouteProvider(self.bench).register(domain, domain)
        self._registered_admin_domain = domain

    def _rollback_admin_domain(self) -> None:
        """Release the just-registered admin route after a failed setup."""
        if self._registered_admin_domain:
            from pilot.core.domain_controller import DomainRouteProvider

            DomainRouteProvider(self.bench).release(self._registered_admin_domain)

    def _release_previous_admin_domain(self) -> None:
        """Free the superseded admin hostname at the provider once the switch is
        committed, so another bench can reuse it without manual cleanup."""
        if self._registered_admin_domain and self._existing_admin_domain:
            from pilot.core.domain_controller import DomainRouteProvider

            DomainRouteProvider(self.bench).release(self._existing_admin_domain)

    def _persist(self, updates: dict) -> None:
        """Merge ``updates`` into bench.toml in place, preserving all other fields."""
        from pilot.config.toml_store import BenchTomlStore

        store = BenchTomlStore.for_bench(self.bench.path)
        data = store.read_raw()
        for section, values in updates.items():
            data.setdefault(section, {}).update(values)
        # Drop the deprecated production.nginx key — nginx is always on in prod.
        data.get("production", {}).pop("nginx", None)
        store.write_raw(data)

    def _write_dns_multitenancy(self) -> None:
        common_config_path = self.bench.sites_path / "common_site_config.json"
        existing_data: dict = {}
        if common_config_path.exists():
            existing_data = json.loads(common_config_path.read_text())
        existing_data["dns_multitenant"] = 1
        common_config_path.write_text(json.dumps(existing_data, indent=2))

    def _setup_supervisor(self) -> None:
        import subprocess

        from pilot.platform import get_package_manager

        pkg = get_package_manager()
        if not pkg.is_installed("supervisor"):
            pkg.install("supervisor")
            subprocess.run(["sudo", "systemctl", "disable", "--now", "supervisor"], check=False)
        from pilot.managers.process_managers.supervisor import SupervisorProcessManager

        mgr = SupervisorProcessManager(self.bench)
        mgr.write_config()
        mgr.install_config()
        mgr.reload_manager_config()

    def _setup_systemd(self) -> None:
        from pilot.managers.process_managers.systemd import SystemdProcessManager

        mgr = SystemdProcessManager(self.bench)
        mgr.write_config()
        mgr.install_config()
        mgr.reload_manager_config()

    def _setup_openrc(self) -> None:
        from pilot.managers.process_managers.openrc import OpenRCProcessManager

        mgr = OpenRCProcessManager(self.bench)
        mgr.write_config()
        mgr.install_config()
        mgr.reload_manager_config()

    def _start_workload(self) -> None:
        """Start the workload (and admin) so the bench is actually serving once
        setup completes — otherwise sites 502 until a separate `bench start`."""
        from pilot.managers.process_manager import ProcessManager

        ProcessManager.for_bench(self.bench).start()

    def _setup_nginx(self) -> None:
        from pilot.commands.setup.nginx import SetupNginxCommand

        SetupNginxCommand(self.bench).run()

    def _setup_letsencrypt_if_needed(self) -> None:
        from pilot.managers.letsencrypt_manager import needs_letsencrypt

        if not needs_letsencrypt(self.bench):
            return
        from pilot.commands.setup.letsencrypt import SetupLetsEncryptCommand

        try:
            SetupLetsEncryptCommand(self.bench).run()
        except Exception as exc:
            if not self._best_effort_tls:
                raise
            print(
                f"Warning: could not obtain a TLS certificate for {self.bench.config.admin.domain} "
                f"yet ({exc}). Continuing on HTTP - retry once its DNS resolves.",
                file=sys.stderr,
            )

    def _build_admin_for_production(self) -> None:
        from pilot.commands.admin import BuildAdminCommand

        BuildAdminCommand().run()

    def _print_summary(self) -> None:
        from pilot.managers.nginx_manager import NginxManager

        nginx_manager = NginxManager(self.bench)
        print("\nProduction setup complete.")
        print("Sites:")
        for site in self.bench.sites():
            if site.config.ssl and nginx_manager.cert_exists(site.config):
                print(f"  https://{site.config.name}")
            else:
                http_port = self.bench.config.nginx.http_port
                port_suffix = "" if http_port == 80 else f":{http_port}"
                print(f"  http://{site.config.name}{port_suffix}")
        admin_https = self.bench.config.admin.tls and nginx_manager.admin_cert_exists()
        scheme = "https" if admin_https else "http"
        print(f"Admin:\n  {scheme}://{self.bench.config.admin.domain}")
