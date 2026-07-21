from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class BenchProduction:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def restart_processes(self) -> None:
        if not self.bench.config.production.enabled:
            return

        from pilot.managers.processes.base import ManagedProcessManager
        from pilot.managers.processes.local import ProcessManager

        manager = cast("ManagedProcessManager", ProcessManager.for_bench(self.bench))
        if not manager.is_configured():
            return
        manager.write_config()
        manager.reload_manager_config()
        manager.restart()

    def remove_production(self, on_progress: Callable[[str], None]) -> None:
        production = self.bench.config.production
        if not production.enabled:
            on_progress(f"Bench {self.bench.config.name} is not deployed to production. Nothing to remove.")
            return

        self._remove_process_manager(production.process_manager)
        self._remove_nginx(on_progress)
        self._persist_production_disabled()
        self._report_removed_production(on_progress)

    def drop(self, on_progress: Callable[[str], None]) -> None:
        import shutil

        name = self.bench.config.name
        self.bench.ensure_no_sites()
        self.remove_production(on_progress)
        self._release_admin_domain()

        from pilot.managers.platform import unmount_legacy_bind_mount

        unmount_legacy_bind_mount(self.bench.path)
        on_progress(f"Deleting {self.bench.path}...")
        shutil.rmtree(self.bench.path, ignore_errors=True)
        on_progress(f"\nBench '{name}' dropped.")

    def setup_nginx(self, on_progress: Callable[[str], None]) -> None:
        from pilot.exceptions import ConfigError
        from pilot.managers.nginx import NginxManager

        if not self.bench.config.production.enabled:
            raise ConfigError(
                "production.enabled must be true in bench.toml to run setup nginx. "
                "Production always uses nginx."
            )
        nginx_manager = NginxManager(self.bench)
        nginx_manager.install()
        self._install_waf()
        (self.bench.config_path / "nginx").mkdir(parents=True, exist_ok=True)
        nginx_manager.generate_config(ssl_ready=True)
        nginx_manager.install_config()
        nginx_manager.setup_sudoers()
        self._report_site_urls(nginx_manager, on_progress)

    def setup_letsencrypt(self) -> None:
        from pilot.exceptions import ConfigError
        from pilot.managers.letsencrypt import LetsEncryptManager
        from pilot.managers.nginx import NginxManager

        if not self.bench.config.letsencrypt.email:
            raise ConfigError("letsencrypt.email must be set in bench.toml to run setup letsencrypt.")
        letsencrypt_manager = LetsEncryptManager(self.bench)
        nginx_manager = NginxManager(self.bench)
        letsencrypt_manager.install()
        letsencrypt_manager.setup_sudoers()
        letsencrypt_manager.ensure_webroot()
        nginx_manager.generate_config(ssl_ready=False)
        nginx_manager.reload()
        letsencrypt_manager.obtain_all()
        nginx_manager.generate_config(ssl_ready=True)
        nginx_manager.reload()

    def setup_production(
        self,
        process_manager: str | None,
        admin_domain: str | None,
        admin_tls: bool | None,
        letsencrypt_email: str | None,
        best_effort_tls: bool,
        on_progress: Callable[[str], None],
    ) -> None:
        from pilot.core.bench.setup import ProductionSetup

        ProductionSetup(
            self.bench,
            process_manager=process_manager,
            admin_domain=admin_domain,
            admin_tls=admin_tls,
            letsencrypt_email=letsencrypt_email,
            best_effort_tls=best_effort_tls,
        ).run(on_progress)

    def _remove_process_manager(self, process_manager: str) -> None:
        if process_manager == "systemd":
            from pilot.managers.processes.systemd import SystemdProcessManager

            SystemdProcessManager(self.bench).remove_units()
        else:
            from pilot.managers.processes.supervisor import SupervisorProcessManager

            SupervisorProcessManager(self.bench).shutdown()

    def _remove_nginx(self, on_progress: Callable[[str], None]) -> None:
        from pilot.managers.nginx import NginxManager

        try:
            NginxManager(self.bench).uninstall_config()
        except Exception as exc:
            on_progress(f"  (nginx cleanup skipped: {exc})")

    def _persist_production_disabled(self) -> None:
        from pilot.config import BenchConfig

        with BenchConfig.open(self.bench.path, mode="raw") as data:
            production = data.setdefault("production", {})
            production["enabled"] = False
            production.pop("process_manager", None)
            production.pop("nginx", None)

    def _report_removed_production(self, on_progress: Callable[[str], None]) -> None:
        from pilot.utils import admin_url

        name = self.bench.config.name
        self.bench.config.production.enabled = False
        self.bench.config.production.process_manager = ""
        on_progress(f"\nProduction deployment removed for {name}.")
        on_progress("\nRun it locally with:")
        on_progress(f"  bench -b {name} start")
        on_progress("\nDevelopment admin:")
        on_progress(f"  {admin_url(self.bench.config)}")

    def _release_admin_domain(self) -> None:
        from pilot.core.adapters.domain_provider import DomainRouteProvider

        domain = self.bench.config.admin.domain
        if domain:
            DomainRouteProvider(self.bench).release(domain)

    def _install_waf(self) -> None:
        from pilot.managers.platform import is_linux

        if not is_linux():
            return
        import sys

        from pilot.managers.waf import WafManager

        try:
            WafManager(self.bench).install()
        except Exception as exc:
            print(
                f"Warning: could not install the WAF (ModSecurity/CRS): {exc}. "
                f"Sites are unaffected; re-run setup to retry.",
                file=sys.stderr,
            )

    def _report_site_urls(self, nginx_manager, on_progress: Callable[[str], None]) -> None:
        tls = self.bench.config.admin.tls
        for site in self.bench.sites():
            if tls and site.config.ssl and nginx_manager.has_cert(site.config):
                on_progress(f"  https://{site.config.name}")
            else:
                http_port = self.bench.config.nginx.http_port
                port_suffix = "" if http_port == 80 else f":{http_port}"
                on_progress(f"  http://{site.config.name}{port_suffix}")
        domain = self.bench.config.admin.domain
        if domain:
            scheme = "https" if tls and nginx_manager.has_admin_cert else "http"
            on_progress(f"  {scheme}://{domain} (admin)")
