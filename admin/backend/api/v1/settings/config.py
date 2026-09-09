from __future__ import annotations

import smtplib

from pilot.config import BenchConfig, FirewallRule, S3Config, WafCondition, WafRule, WorkerGroup
from pilot.config.alert_limit import RESOURCE_LIMIT_FIELDS
from pilot.config.llm import LLMConfig
from pilot.config.mail import MailConfig
from pilot.core.alerts import check_mail_credentials


def _coerce_int(value):
    """Let config validation reject bad numeric input with a clean API error."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


class ConfigPatcher:
    def __init__(self, config: BenchConfig, data: dict, mail: MailConfig | None = None) -> None:
        self.config = config
        self.data = data
        self.mail = mail or MailConfig()

    def apply(self) -> str | None:
        self._apply_bench()
        self._apply_lite_mode()
        self._apply_workers()
        self._apply_firewall()
        self._apply_waf()
        if error := self._apply_llm():
            return error
        if error := self._apply_s3():
            return error
        if error := self._apply_resource_limits():
            return error
        if error := self._apply_mail():
            return error
        try:
            self.config.validate()
        except Exception as error:
            return str(error)
        return None

    def _apply_bench(self) -> None:
        bench = self.data.get("bench") or {}
        if "http_port" in bench:
            self.config.http_port = int(bench["http_port"])
        if "socketio_port" in bench:
            self.config.socketio_port = int(bench["socketio_port"])
        if "default_branch" in bench:
            self.config.default_branch = str(bench["default_branch"]).strip()
        if "allow_developer_mode" in bench:
            self.config.allow_developer_mode = bool(bench["allow_developer_mode"])

    def _apply_lite_mode(self) -> None:
        lite_mode = self.data.get("lite_mode") or {}
        if "enabled" in lite_mode:
            self.config.lite_mode.enabled = bool(lite_mode["enabled"])

    def _apply_workers(self) -> None:
        workers = self.data.get("workers")
        if not workers:
            return
        groups = []
        for entry in workers:
            queues = entry.get("queues") or []
            if isinstance(queues, str):
                queues = [queue.strip() for queue in queues.split(",") if queue.strip()]
            queues = [str(queue) for queue in queues if str(queue).strip()]
            if not queues:
                continue
            groups.append(WorkerGroup(queues=queues, count=int(entry.get("count", 1))))
        if groups:
            self.config.workers.groups = groups

    def _apply_resource_limits(self) -> str | None:
        resource_limits = self.data.get("resource_limits")
        if not resource_limits:
            return None
        limits = self.config.resource_limits
        for name in RESOURCE_LIMIT_FIELDS:
            if name in resource_limits:
                setattr(limits, name, _coerce_int(resource_limits[name]))
        if "site_uptime" in resource_limits:
            limits.site_uptime = bool(resource_limits["site_uptime"])
        if "webhook_endpoints" in resource_limits:
            limits.webhook_endpoints = self._webhook_endpoints(
                resource_limits["webhook_endpoints"] or [], limits.webhook_endpoints
            )
        if "email_recipients" in resource_limits:
            limits.email_recipients = [
                address.strip()
                for address in map(str, resource_limits["email_recipients"] or [])
                if address.strip()
            ]
        try:
            limits.validate()
        except ValueError as error:
            return str(error)
        return None

    def _apply_mail(self) -> str | None:
        """A blank password keeps the stored one, the same way webhook tokens work.
        Clearing the server drops it, so a rotated credential has a way out."""
        mail_data = self.data.get("mail")
        if not mail_data:
            return None
        error = self._patch_mail_fields(mail_data)
        if error:
            return error
        try:
            self.mail.validate()
        except ValueError as error:
            return str(error)
        return self._check_mail()

    def _patch_mail_fields(self, mail_data: dict) -> str | None:
        had_password = bool(self.mail.password)
        previous_server = self.mail.server
        for name in ("server", "email", "login"):
            if name in mail_data:
                setattr(self.mail, name, str(mail_data[name]).strip())
        if "port" in mail_data:
            self.mail.port = _coerce_int(mail_data["port"] or 0)
        if "use_ssl" in mail_data:
            self.mail.use_ssl = bool(mail_data["use_ssl"])
        if mail_data.get("password"):
            self.mail.password = str(mail_data["password"])
        if not self.mail.server:
            self.mail.password = ""
            return None
        # The stored password belongs to the server it was saved for. Sending it to
        # a newly named host would disclose it there, and dropping it silently would
        # save an unauthenticated mailbox, so ask for it again.
        if had_password and self.mail.server != previous_server and not mail_data.get("password"):
            self.mail.password = ""
            return "Enter the password for the new mail server."
        return None

    def _check_mail(self) -> str | None:
        """Prove the settings can actually reach the server before they are stored,
        the way the framework's Email Account opens a session on save."""
        if not self.mail.server:
            return None
        try:
            check_mail_credentials(self.mail)
        except smtplib.SMTPAuthenticationError:
            return "The mail server rejected that login name and password."
        except (OSError, smtplib.SMTPException) as error:
            return f"Could not reach the mail server: {error}"
        return None

    @staticmethod
    def _webhook_endpoints(entries: list[dict], stored: dict[str, str]) -> dict[str, str]:
        """A blank token keeps the stored one, found by the URL the row was loaded
        with so editing the URL does not drop it."""
        endpoints: dict[str, str] = {}
        for entry in entries:
            url = str(entry.get("url", "")).strip()
            if not url:
                continue
            token = str(entry.get("token", "")).strip()
            loaded_as = str(entry.get("original_url", "")).strip() or url
            endpoints[url] = token or stored.get(loaded_as, "")
        return endpoints

    def _apply_firewall(self) -> None:
        firewall = self.data.get("firewall")
        if firewall is None:
            return
        firewall_config = self.config.firewall
        if "enabled" in firewall:
            firewall_config.enabled = bool(firewall["enabled"])
        if "default" in firewall:
            firewall_config.default = str(firewall["default"])
        if "rules" in firewall:
            firewall_config.rules = self._parse_firewall_rules(firewall["rules"] or [])

    @staticmethod
    def _parse_firewall_rules(entries: list[dict]) -> list[FirewallRule]:
        rules = []
        for entry in entries:
            ip = str(entry.get("ip", "")).strip()
            if not ip:
                continue
            rules.append(
                FirewallRule(
                    ip=ip,
                    action=str(entry.get("action", "deny")),
                    description=str(entry.get("description", "")).strip(),
                )
            )
        return rules

    def _apply_waf(self) -> None:
        waf = self.data.get("waf")
        if waf is None:
            return
        waf_config = self.config.waf
        self._apply_waf_scalars(waf, waf_config)
        self._apply_waf_lists(waf, waf_config)

    @staticmethod
    def _apply_waf_scalars(waf: dict, waf_config) -> None:
        if "enabled" in waf:
            waf_config.enabled = bool(waf["enabled"])
        if "mode" in waf:
            waf_config.mode = str(waf["mode"])
        if "paranoia" in waf:
            waf_config.paranoia = _coerce_int(waf["paranoia"])
        if "inbound_threshold" in waf:
            waf_config.inbound_threshold = _coerce_int(waf["inbound_threshold"])
        if "body_limit" in waf:
            waf_config.body_limit = str(waf["body_limit"]).strip()
        if "inspect_responses" in waf:
            waf_config.inspect_responses = bool(waf["inspect_responses"])

    def _apply_waf_lists(self, waf: dict, waf_config) -> None:
        if "exclusions" in waf:
            waf_config.exclusions = [
                str(line).strip() for line in (waf["exclusions"] or []) if str(line).strip()
            ]
        if "exempt_paths" in waf:
            waf_config.exempt_paths = [
                str(path).strip() for path in (waf["exempt_paths"] or []) if str(path).strip()
            ]
        if "custom_rules" in waf:
            waf_config.custom_rules = [self._parse_waf_rule(rule) for rule in (waf["custom_rules"] or [])]

    @staticmethod
    def _parse_waf_rule(data: dict) -> WafRule:
        conditions = [
            WafCondition(
                field=str(condition.get("field", "")).strip(),
                operator=str(condition.get("operator", "")).strip(),
                value=str(condition.get("value", "")).strip(),
                header_name=str(condition.get("header_name", "")).strip(),
            )
            for condition in (data.get("conditions") or [])
            if str(condition.get("value", "")).strip() or str(condition.get("field", "")).strip()
        ]
        return WafRule(
            name=str(data.get("name", "")).strip(),
            action=str(data.get("action", "block")).strip(),
            match=str(data.get("match", "all")).strip(),
            enabled=bool(data.get("enabled", True)),
            conditions=conditions,
        )

    def _apply_s3(self) -> str | None:
        s3 = self.data.get("s3") or {}
        if not s3:
            return None
        if s3.get("disconnect"):
            self.config.s3 = S3Config()
            return None

        s3_config = self.config.s3
        self._update_s3_config(s3, s3_config)
        if not self._s3_has_any_value(s3_config):
            return None
        if not self._s3_is_complete(s3_config):
            return "s3.access_key, s3.secret_key, s3.bucket, s3.provider, and s3.region are all required."
        return self._validate_s3_region(s3_config)

    @staticmethod
    def _update_s3_config(s3: dict, s3_config: S3Config) -> None:
        if "access_key" in s3:
            s3_config.access_key = str(s3["access_key"]).strip()
        secret_key = str(s3.get("secret_key", "")).strip()
        if secret_key:
            s3_config.secret_key = secret_key
        if "bucket" in s3:
            s3_config.bucket = str(s3["bucket"]).strip()
        if "provider" in s3:
            s3_config.provider = str(s3["provider"]).strip()
        if "region" in s3:
            s3_config.region = str(s3["region"]).strip()

    @staticmethod
    def _s3_has_any_value(s3_config: S3Config) -> bool:
        return bool(
            s3_config.access_key
            or s3_config.secret_key
            or s3_config.bucket
            or s3_config.provider
            or s3_config.region
        )

    @staticmethod
    def _s3_is_complete(s3_config: S3Config) -> bool:
        return bool(
            s3_config.access_key
            and s3_config.secret_key
            and s3_config.bucket
            and s3_config.provider
            and s3_config.region
        )

    @staticmethod
    def _validate_s3_region(s3_config: S3Config) -> str | None:
        from pilot.integrations.s3.base import SUPPORTED_REGIONS

        if s3_config.provider not in SUPPORTED_REGIONS:
            return f"s3.provider must be one of: {', '.join(SUPPORTED_REGIONS)}"
        if s3_config.region not in SUPPORTED_REGIONS[s3_config.provider]:
            return f"s3.region '{s3_config.region}' is not valid for provider '{s3_config.provider}'."

        return None

    def _apply_llm(self) -> str | None:
        llm = self.data.get("llm") or {}
        if not llm:
            return None
        if llm.get("disconnect"):
            self.config.llm = LLMConfig()
            return None

        llm_config = self.config.llm
        if "provider" in llm:
            llm_config.provider = str(llm["provider"]).strip()
        if "model" in llm:
            llm_config.model = str(llm["model"]).strip()
        if "api_base" in llm:
            llm_config.api_base = str(llm["api_base"]).strip()
        api_key = str(llm.get("api_key", "")).strip()
        if api_key:
            llm_config.api_key = api_key
        if "max_tokens" in llm:
            llm_config.max_tokens = _coerce_int(llm["max_tokens"])
        return self._validate_llm_provider(llm_config)

    @staticmethod
    def _validate_llm_provider(llm_config: LLMConfig) -> str | None:
        from pilot.integrations.llm.registry import known_providers, requires_api_base

        if not llm_config.provider:
            return None
        if llm_config.provider not in known_providers():
            return f"llm.provider {llm_config.provider!r} is not a supported provider."
        if not llm_config.model:
            return "llm.model is required. Select a model for the provider."
        if requires_api_base(llm_config.provider) and not llm_config.api_base:
            return (
                f"llm.api_base is required for {llm_config.provider!r} - it is served from "
                "your own endpoint, so there is no default to fall back to."
            )
        return None
