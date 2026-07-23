from __future__ import annotations

from pathlib import Path

from pilot.config import BenchConfig, FirewallRule, S3Config, WafCondition, WafRule, WorkerGroup


def _coerce_int(value):
    """Let config validation reject bad numeric input with a clean API error."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


class ConfigPatcher:
    def __init__(self, config: BenchConfig, data: dict) -> None:
        self.config = config
        self.data = data

    def apply(self) -> str | None:
        self._apply_bench()
        self._apply_mariadb()
        self._apply_postgres()
        self._apply_redis()
        self._apply_workers()
        self._apply_firewall()
        self._apply_waf()
        self._apply_admin()
        self._apply_monitor()
        if error := self._apply_s3():
            return error
        if error := self._apply_production():
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
        if "developer_mode" in bench:
            self.config.developer_mode = bool(bench["developer_mode"])

    def _apply_mariadb(self) -> None:
        mariadb = self.data.get("mariadb") or {}
        if not mariadb:
            return
        mariadb_config = self.config.mariadb
        mariadb_config.host = str(mariadb.get("host", mariadb_config.host))
        mariadb_config.port = int(mariadb.get("port", mariadb_config.port))
        mariadb_config.admin_user = str(mariadb.get("admin_user", mariadb_config.admin_user))
        mariadb_config.socket_path = str(mariadb.get("socket_path", mariadb_config.socket_path))

    def _apply_postgres(self) -> None:
        postgres = self.data.get("postgres") or {}
        if not postgres:
            return
        postgres_config = self.config.postgres
        postgres_config.host = str(postgres.get("host", postgres_config.host))
        postgres_config.port = int(postgres.get("port", postgres_config.port))
        postgres_config.admin_user = str(postgres.get("admin_user", postgres_config.admin_user))
        password = str(postgres.get("root_password", "")).strip()
        if password:
            postgres_config.root_password = password

    def _apply_redis(self) -> None:
        redis = self.data.get("redis") or {}
        if not redis:
            return
        redis_config = self.config.redis
        redis_config.cache_port = int(redis.get("cache_port", redis_config.cache_port))
        redis_config.queue_port = int(redis.get("queue_port", redis_config.queue_port))

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

    def _apply_admin(self) -> None:
        admin = self.data.get("admin") or {}
        if "tls" in admin:
            self.config.admin.tls = bool(admin["tls"])
        letsencrypt = self.data.get("letsencrypt") or {}
        if "email" in letsencrypt:
            self.config.letsencrypt.email = str(letsencrypt["email"]).strip()

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

    def _apply_monitor(self) -> None:
        monitor = self.data.get("monitor") or {}
        if not monitor:
            return
        monitor_config = self.config.monitor
        if "system_log_path" in monitor and str(monitor["system_log_path"]).strip():
            monitor_config.system_log_path = Path(str(monitor["system_log_path"]).strip())
        if "log_path" in monitor:
            value = str(monitor["log_path"]).strip()
            monitor_config.log_path = Path(value) if value else None
        if "system_log_max_size" in monitor and str(monitor["system_log_max_size"]).strip():
            monitor_config.system_log_max_size = str(monitor["system_log_max_size"]).strip()
        if "application_log_max_size" in monitor and str(monitor["application_log_max_size"]).strip():
            monitor_config.application_log_max_size = str(monitor["application_log_max_size"]).strip()

    def _apply_production(self) -> str | None:
        production = self.data.get("production") or {}
        if not production:
            return None
        if "process_manager" in production:
            from pilot.config import VALID_PROCESS_MANAGERS

            process_manager = str(production["process_manager"])
            valid = ("none", *VALID_PROCESS_MANAGERS)
            if process_manager not in valid:
                return f"process_manager must be one of: {', '.join(valid)}"
            process_manager_name = "" if process_manager == "none" else process_manager
            self.config.production.process_manager = process_manager_name
            self.config.production.enabled = process_manager_name != ""
        return None
