from __future__ import annotations

from pathlib import Path

from pilot.config import BenchConfig
from pilot.config.mail import MailConfig
from pilot.config.webhook import WEBHOOK_EVENTS


class SettingsPayload:
    """The settings sections the Admin UI shows, with secrets reduced to "is set" flags."""

    def __init__(self, config: BenchConfig) -> None:
        self.config = config

    @property
    def worker_groups(self) -> list[dict]:
        """Every worker group as a dict."""
        return [group.to_dict() for group in self.config.workers.groups]

    @property
    def firewall(self) -> dict:
        """The firewall switch, default action and rules."""
        firewall = self.config.firewall
        return {
            "enabled": firewall.enabled,
            "default": firewall.default,
            "rules": [
                {"ip": rule.ip, "action": rule.action, "description": rule.description}
                for rule in firewall.rules
            ],
        }

    @property
    def proxy(self) -> dict:
        """The edge proxy settings."""
        return {"cloudflare": self.config.proxy.cloudflare}

    @property
    def waf(self) -> dict:
        """The web application firewall settings and custom rules."""
        waf = self.config.waf
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

    @property
    def s3(self) -> dict:
        """The backup storage settings, without the secret key."""
        s3 = self.config.s3
        return {
            "access_key": s3.access_key,
            "secret_key_set": bool(s3.secret_key),
            "bucket": s3.bucket,
            "provider": s3.provider,
            "region": s3.region,
            "endpoint_url": s3.endpoint_url,
            "upload_migration_backups": s3.upload_migration_backups,
        }

    @property
    def llm(self) -> dict:
        """The language model settings, without the API key."""
        llm = self.config.llm
        return {
            "provider": llm.provider,
            "api_key_set": bool(llm.api_key),
            "model": llm.model,
            "max_tokens": llm.max_tokens,
            "api_base": llm.api_base,
        }

    @property
    def resource_limits(self) -> dict:
        """The alert thresholds and their email recipients."""
        limits = self.config.resource_limits
        return {
            "cpu_usage_limit": limits.cpu_usage_limit,
            "memory_usage_limit": limits.memory_usage_limit,
            "disk_space_limit": limits.disk_space_limit,
            "site_uptime": limits.site_uptime,
            "email_recipients": limits.email_recipients,
        }

    @property
    def webhook_endpoints(self) -> list[dict]:
        """Every webhook endpoint, without its token."""
        return [
            {"url": endpoint.url, "token_set": bool(endpoint.token), "events": endpoint.events}
            for endpoint in self.config.webhook_endpoints
        ]

    @property
    def webhook_events(self) -> list[dict]:
        """The events a webhook can subscribe to, with their labels."""
        return [{"value": name, "label": label} for name, label in WEBHOOK_EVENTS.items()]

    @property
    def restart_triggers(self) -> dict:
        """The values whose change needs regenerated configs and a restart."""
        config = self.config
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
            "workers": {"groups": self.worker_groups},
            "production": {"process_manager": config.production.process_manager or "none"},
            "lite_mode": {"enabled": config.lite_mode.enabled},
        }

    def get_active_tokens(self, bench_root: Path | None) -> list[dict]:
        """The Admin sessions that are still valid, oldest expiry first."""
        if bench_root is None:
            return []
        from admin.backend.internal.session import Session
        from pilot.core.bench import Bench

        sessions = Session(Bench(self.config, bench_root)).active_sessions()
        return [
            {"jti": jti, **record}
            for jti, record in sorted(sessions.items(), key=lambda item: item[1]["exp"])
        ]

    @staticmethod
    def get_mail(mail: MailConfig) -> dict:
        """The outgoing mail settings, without the password."""
        return {
            "server": mail.server,
            "port": mail.port,
            "email": mail.email,
            "login": mail.login,
            "use_ssl": mail.use_ssl,
            "password_set": bool(mail.password),
        }
