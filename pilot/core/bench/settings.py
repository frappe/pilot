from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config import BenchConfig
from pilot.managers.waf import WafManager

if TYPE_CHECKING:
    from pilot.core.bench import Bench


_RESTART_KEYS = {
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
}


class SettingsApplyFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class BenchSettings:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def apply_saved_settings(
        self,
        old_restart: dict,
        old_firewall: dict,
        old_waf: dict,
        old_s3_config: dict,
    ) -> tuple[bool, str | None]:
        restarted = self._regenerate_and_restart_if_needed(old_restart)
        waf_warning = self._apply_nginx_if_needed(old_firewall, old_waf)
        self._sync_s3_if_needed(old_s3_config)
        self.bench.set_developer_mode(self.bench.config.developer_mode)
        return restarted, waf_warning

    def _regenerate_and_restart_if_needed(self, old_restart: dict) -> bool:
        if not is_restart_needed(old_restart, restart_trigger_values(self.bench.config)):
            return False
        try:
            regenerate_configs(self.bench)
        except Exception as error:
            raise SettingsApplyFailed(
                "configuration_generation_failed",
                "Settings were saved, but service configuration could not be regenerated.",
            ) from error
        try:
            return restart_running_workload(self.bench)
        except Exception as error:
            raise SettingsApplyFailed(
                "service_restart_failed",
                "Settings were saved, but running services could not be restarted.",
            ) from error

    def _apply_nginx_if_needed(self, old_firewall: dict, old_waf: dict) -> str | None:
        waf_changed = waf_payload(self.bench.config) != old_waf
        if not self.bench.config.production.enabled:
            return None
        if firewall_payload(self.bench.config) == old_firewall and not waf_changed:
            return None

        warning = None
        if waf_changed and self.bench.config.waf.enabled and not WafManager.is_installed():
            warning = (
                "ModSecurity is not installed on this host. Redeploy production to "
                "install the WAF; it stays inactive until then."
            )
        try:
            regenerate_nginx(self.bench)
        except Exception as error:
            raise SettingsApplyFailed(
                "nginx_apply_failed",
                "Settings were saved, but nginx could not apply them.",
            ) from error
        return warning

    def _sync_s3_if_needed(self, old_s3_config: dict) -> None:
        if s3_payload(self.bench.config) == old_s3_config:
            return
        try:
            self.bench.sync_s3_credentials(self.bench.config.s3)
        except Exception as error:
            raise SettingsApplyFailed(
                "s3_sync_failed",
                "Settings were saved, but site backup configuration could not be synchronized.",
            ) from error


def is_restart_needed(old: dict, new: dict) -> bool:
    return any(
        old.get(section, {}).get(key) != new.get(section, {}).get(key) for section, key in _RESTART_KEYS
    )


def worker_groups_payload(config: BenchConfig) -> list[dict]:
    return [{"queues": list(group.queues), "count": group.count} for group in config.workers.groups]


def firewall_payload(config: BenchConfig) -> dict:
    firewall = config.firewall
    return {
        "enabled": firewall.enabled,
        "default": firewall.default,
        "rules": [
            {"ip": rule.ip, "action": rule.action, "description": rule.description} for rule in firewall.rules
        ],
    }


def waf_payload(config: BenchConfig) -> dict:
    waf = config.waf
    return {
        "enabled": waf.enabled,
        "mode": waf.mode,
        "paranoia": waf.paranoia,
        "inbound_threshold": waf.inbound_threshold,
        "body_limit": waf.body_limit,
        "inspect_responses": waf.inspect_responses,
        "exclusions": list(waf.exclusions),
        "exempt_paths": list(waf.exempt_paths),
        "custom_rules": [
            {
                "name": rule.name,
                "action": rule.action,
                "match": rule.match,
                "enabled": rule.enabled,
                "conditions": [
                    {
                        "field": condition.field,
                        "operator": condition.operator,
                        "value": condition.value,
                        "header_name": condition.header_name,
                    }
                    for condition in rule.conditions
                ],
            }
            for rule in waf.custom_rules
        ],
    }


def s3_payload(config: BenchConfig) -> dict:
    return {
        "access_key": config.s3.access_key,
        "secret_key_set": bool(config.s3.secret_key),
        "bucket": config.s3.bucket,
        "provider": config.s3.provider,
        "region": config.s3.region,
    }


def restart_trigger_values(config: BenchConfig) -> dict:
    return {
        "bench": {
            "python": config.python_version,
            "http_port": config.http_port,
            "socketio_port": config.socketio_port,
        },
        "mariadb": {
            "host": config.mariadb.host,
            "port": config.mariadb.port,
            "admin_user": config.mariadb.admin_user,
            "socket_path": config.mariadb.socket_path,
        },
        "redis": {"cache_port": config.redis.cache_port, "queue_port": config.redis.queue_port},
        "workers": {"groups": worker_groups_payload(config)},
        "production": {"process_manager": config.production.process_manager or "none"},
    }


def regenerate_configs(bench: "Bench") -> None:
    from pilot.managers.processes.local import ProcessManager
    from pilot.managers.redis import RedisManager

    RedisManager(bench.config.redis, bench).generate_configs()
    ProcessManager.for_bench(bench).write_config()


def regenerate_nginx(bench: "Bench") -> None:
    from pilot.managers.nginx import NginxManager

    manager = NginxManager(bench)
    manager.generate_config(ssl_ready=True)
    manager.install_config()


def restart_running_workload(bench: "Bench") -> bool:
    from pilot.managers.processes.local import ProcessManager
    from pilot.managers.processes.supervisor import SupervisorProcessManager
    from pilot.managers.processes.systemd import SystemdProcessManager

    manager = ProcessManager.detect_running(bench)
    if isinstance(manager, SupervisorProcessManager):
        return _restart_supervisor(manager, bench.config.name)
    if isinstance(manager, SystemdProcessManager):
        return _restart_systemd(manager)
    return False


def _restart_supervisor(manager, bench_name: str) -> bool:
    if not manager.is_alive():
        return False
    subprocess.run(
        [*manager._supervisorctl(), "reread"],
        capture_output=True,
        timeout=10,
        check=True,
    )
    subprocess.run(
        [*manager._supervisorctl(), "update"],
        capture_output=True,
        timeout=10,
        check=True,
    )
    programs = _non_admin_supervisor_programs(manager.supervisor_conf_path, bench_name)
    if not programs:
        return False
    subprocess.run(
        [*manager._supervisorctl(), "restart", *programs],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return True


def _restart_systemd(manager) -> bool:
    if not manager.is_running():
        return False
    env = manager._systemctl_env()
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True,
        env=env,
        timeout=10,
        check=True,
    )
    non_admin_units = [
        manager._unit_name(process.name)
        for process in manager._prod_process_definitions()
        if process.name != "admin"
    ]
    if not non_admin_units:
        return False
    subprocess.run(
        [*manager._systemctl(), "restart", *non_admin_units],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=True,
    )
    return True


def _non_admin_supervisor_programs(conf: Path, bench_name: str) -> list[str]:
    result = subprocess.run(
        ["supervisorctl", "-c", str(conf), "status", f"{bench_name}:*"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return [
        line.split()[0]
        for line in result.stdout.splitlines()
        if line.strip() and not line.split()[0].endswith("-admin")
    ]
