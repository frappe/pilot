from __future__ import annotations

import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from pilot.config import BenchConfig
from pilot.config.proxy import is_cloudflare_address
from pilot.core.bench.settings.payload import SettingsPayload
from pilot.managers.waf import WafManager

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.managers.processes.supervisor import SupervisorProcessManager
    from pilot.managers.processes.systemd import SystemdProcessManager


class SettingsApplyFailed(Exception):
    """Settings were saved, but a follow-up step such as a restart or nginx reload failed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class BenchSettings:
    """Applies saved bench settings to the running services."""

    # (section, key) pairs in SettingsPayload.restart_triggers whose change needs a restart.
    restart_keys = frozenset(
        {
            ("bench", "python"),
            ("bench", "http_port"),
            ("bench", "socketio_port"),
            ("mariadb", "host"),
            ("mariadb", "port"),
            ("mariadb", "admin_user"),
            ("mariadb", "socket_path"),
            ("redis", "cache_port"),
            ("redis", "queue_port"),
            ("workers", "groups"),
            ("production", "process_manager"),
            ("lite_mode", "enabled"),
        }
    )

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def apply_saved_settings(
        self,
        old_restart: dict,
        old_firewall: dict,
        old_waf: dict,
        old_s3_config: dict,
        old_proxy: dict,
    ) -> tuple[bool, str | None]:
        """Apply what changed since the given snapshots; return (restarted, WAF warning)."""
        self.bench.enforce_lite_mode_rules()
        restarted = self.restart_if_needed(old_restart)
        waf_warning = self.apply_nginx_if_needed(old_firewall, old_waf, old_proxy)
        self.sync_s3_if_needed(old_s3_config)
        return restarted, waf_warning

    def restart_if_needed(self, old_restart: dict) -> bool:
        """Regenerate configs and restart the workload when a restart trigger changed."""
        new_restart = SettingsPayload(self.bench.config).restart_triggers
        if not self.bench.supports_lite_mode:
            # Without Frappe's runner the bench runs the classic process set either way.
            old_restart = {**old_restart, "lite_mode": new_restart["lite_mode"]}
        if not self.is_restart_needed(old_restart, new_restart):
            return False
        if old_restart.get("lite_mode") != new_restart.get("lite_mode"):
            # The process set itself changed, so the units have to be reinstalled.
            from pilot.tasks.switch_lite_mode import SwitchLiteModeTask

            SwitchLiteModeTask.queue(self.bench)
            return True
        try:
            self.regenerate_configs()
        except Exception as error:
            raise SettingsApplyFailed(
                "configuration_generation_failed",
                "Settings were saved, but service configuration could not be regenerated.",
            ) from error
        try:
            return self.restart_running_workload()
        except Exception as error:
            raise SettingsApplyFailed(
                "service_restart_failed",
                "Settings were saved, but running services could not be restarted.",
            ) from error
        finally:
            # A timed-out restart still finishes, so nginx follows the saved config.
            if self.bench.config.production.enabled:
                self.apply_nginx()

    @classmethod
    def is_restart_needed(cls, old: dict, new: dict) -> bool:
        """Whether any restart trigger differs between two SettingsPayload.restart_triggers."""
        return any(
            old.get(section, {}).get(key) != new.get(section, {}).get(key)
            for section, key in cls.restart_keys
        )

    def get_cloudflare_status(self) -> dict:
        """Report the edge proxy and which site domains resolve to Cloudflare."""
        from pilot.core.adapters.domain_provider import DomainRouteProvider

        domains = sorted({domain for site in self.bench.sites() for domain in site.config.all_domains})
        # Each lookup can wait on an upstream resolver, so they run side by side, not one after another.
        with ThreadPoolExecutor(max_workers=min(16, len(domains) or 1)) as pool:
            behind = list(pool.map(self.is_behind_cloudflare, domains))
        return {
            "has_edge_proxy": bool(DomainRouteProvider.proxy_servers()),
            "domains": [
                {"domain": domain, "is_behind_cloudflare": proxied}
                for domain, proxied in zip(domains, behind, strict=True)
            ],
        }

    @classmethod
    def is_behind_cloudflare(cls, domain: str) -> bool:
        """Whether every address the domain resolves to belongs to Cloudflare."""
        addresses = cls.get_addresses(domain, socket.AF_INET) or cls.get_addresses(domain, socket.AF_INET6)
        return bool(addresses) and all(is_cloudflare_address(address) for address in addresses)

    @staticmethod
    def get_addresses(domain: str, family: socket.AddressFamily) -> set[str]:
        """The domain's addresses in one family; empty when it does not resolve."""
        # One family at a time: a resolver that fails AAAA slowly would hold up every A answer.
        try:
            return {str(info[4][0]) for info in socket.getaddrinfo(domain, None, family)}
        except OSError:
            return set()

    def apply_nginx_if_needed(self, old_firewall: dict, old_waf: dict, old_proxy: dict) -> str | None:
        """Reload nginx when the firewall, WAF or proxy changed; return a WAF warning, if any."""
        payload = SettingsPayload(self.bench.config)
        waf_changed = payload.waf != old_waf
        proxy_changed = payload.proxy != old_proxy
        if proxy_changed:
            self.apply_nginx_to_host()
        if not self.bench.config.production.enabled:
            return None
        if payload.firewall == old_firewall and not waf_changed:
            return None

        warning = None
        if waf_changed and self.bench.config.waf.enabled and not WafManager.is_installed():
            warning = (
                "ModSecurity is not installed on this host. Redeploy production to "
                "install the WAF; it stays inactive until then."
            )
        if not proxy_changed:
            self.apply_nginx()
        return warning

    def apply_nginx_to_host(self) -> None:
        """Re-render every production bench, since the proxy settings are host-wide."""
        from pilot.core.bench import Bench

        for path in sorted(self.bench.path.parent.iterdir()):
            if not (path / "bench.toml").exists():
                continue
            try:
                bench = Bench(BenchConfig.read(path), path)
            except Exception as error:
                raise SettingsApplyFailed(
                    "nginx_apply_failed",
                    f"Settings were saved, but bench '{path.name}' could not be read to apply them.",
                ) from error
            if bench.config.production.enabled:
                BenchSettings(bench).apply_nginx()

    def apply_nginx(self) -> None:
        """Publish this bench's nginx config, reporting a failure as SettingsApplyFailed."""
        try:
            self.regenerate_nginx()
        except Exception as error:
            raise SettingsApplyFailed(
                "nginx_apply_failed",
                "Settings were saved, but nginx could not apply them.",
            ) from error

    def sync_s3_if_needed(self, old_s3_config: dict) -> None:
        """Push changed backup storage credentials to every site."""
        if SettingsPayload(self.bench.config).s3 == old_s3_config:
            return
        try:
            self.bench.sync_s3_credentials(self.bench.config.s3)
        except Exception as error:
            raise SettingsApplyFailed(
                "s3_sync_failed",
                "Settings were saved, but site backup configuration could not be synchronized.",
            ) from error

    def regenerate_configs(self) -> None:
        """Rewrite the Redis, process manager and common site configs from bench.toml."""
        from pilot.managers.processes.local import ProcessManager
        from pilot.managers.redis import RedisManager

        RedisManager(self.bench.config.redis, self.bench).generate_configs()
        ProcessManager.for_bench(self.bench).write_config()
        self.bench.write_common_site_config()

    def regenerate_nginx(self) -> None:
        """Publish this bench's nginx config."""
        from pilot.managers.nginx import NginxManager

        NginxManager(self.bench).publish_config(ssl_ready=True)

    def restart_running_workload(self) -> bool:
        """Restart the workload under whichever process manager runs it; False when none does."""
        from pilot.managers.processes.local import ProcessManager
        from pilot.managers.processes.supervisor import SupervisorProcessManager
        from pilot.managers.processes.systemd import SystemdProcessManager

        manager = ProcessManager.detect_running(self.bench)
        if isinstance(manager, SupervisorProcessManager):
            return self.restart_supervisor(manager)
        if isinstance(manager, SystemdProcessManager):
            return self.restart_systemd(manager)
        return False

    @staticmethod
    def restart_supervisor(manager: "SupervisorProcessManager") -> bool:
        """Reload the supervisor programs and restart the workload; False when supervisor is down."""
        from pilot.managers.processes.base import UnitGroup

        if not manager.is_alive():
            return False
        for action in ("reread", "update"):
            subprocess.run([*manager._supervisorctl(), action], capture_output=True, timeout=10, check=True)
        manager.apply_unit_action("restart", UnitGroup.WORKLOAD)
        return True

    @staticmethod
    def restart_systemd(manager: "SystemdProcessManager") -> bool:
        """Relink the units and restart every process but admin; False when nothing runs."""
        if not manager.is_running():
            return False
        env = manager._systemctl_env()
        manager.link_units()
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            env=env,
            timeout=10,
            check=True,
        )
        # The web port may have changed, and the socket keeps its old one until it restarts.
        manager.reopen_sockets()
        definitions = [process for process in manager._prod_process_definitions() if process.name != "admin"]
        if not definitions:
            return False
        manager.restart_processes(definitions)
        return True
