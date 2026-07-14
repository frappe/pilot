import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from pilot.config.admin_config import AdminConfig
from pilot.config.app_config import AppConfig
from pilot.config.central_config import CentralConfig
from pilot.config.firewall_config import FirewallConfig, FirewallRule
from pilot.config.gunicorn_config import GunicornConfig
from pilot.config.letsencrypt_config import LetsEncryptConfig
from pilot.config.mariadb_config import MariaDBConfig
from pilot.config.monitor_config import MonitorConfig
from pilot.config.nginx_config import NginxConfig
from pilot.config.postgres_config import PostgresConfig
from pilot.config.production_config import ProductionConfig
from pilot.config.redis_config import RedisConfig
from pilot.config.s3_config import S3Config
from pilot.config.worker_config import WorkerConfig, WorkerGroup
from pilot.exceptions import ConfigError

_BENCH_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
_EMAIL_PATTERN = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
_VERSION_PATTERN = re.compile(r"^\d+(\.\d+)*$")
# Lenient hostname: dotted labels of alphanumerics/hyphens. Allows dev names
# like "admin1.localhost" and real domains like "admin.example.com".
_HOSTNAME_PATTERN = re.compile(r"^(?=.{1,253}$)[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$")
_REDIS_PORT_MIN = 1024
_REDIS_PORT_MAX = 65535
_PORT_MIN = 1
_PORT_MAX = 65535


@dataclass
class BenchConfig:
    name: str
    python_version: str
    mariadb: MariaDBConfig
    redis: RedisConfig
    workers: WorkerConfig
    postgres: PostgresConfig = field(default_factory=PostgresConfig)
    apps: List[AppConfig] = field(default_factory=list)
    http_port: int = 8000
    socketio_port: int = 9000
    socketio_backend: str = "node"
    watch_apps_js: bool = False
    reload_python: bool = False
    watch_admin_js: bool = False
    # The single database engine for this bench's sites: "mariadb" or "postgres".
    db_type: str = "mariadb"
    default_branch: str = ""
    production: ProductionConfig = field(default_factory=ProductionConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    nginx: NginxConfig = field(default_factory=NginxConfig)
    gunicorn: GunicornConfig = field(default_factory=GunicornConfig)
    letsencrypt: LetsEncryptConfig = field(default_factory=LetsEncryptConfig)
    admin: AdminConfig = field(default_factory=AdminConfig)
    central: CentralConfig = field(default_factory=CentralConfig)
    firewall: FirewallConfig = field(default_factory=FirewallConfig)
    s3: S3Config = field(default_factory=S3Config)

    @classmethod
    def from_file(cls, path: Path) -> "BenchConfig":
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        config = cls._from_dict(data)
        config.validate()
        return config

    @classmethod
    def _from_dict(cls, data: dict) -> "BenchConfig":
        bench_data = data.get("bench", {})
        apps = [
            AppConfig(
                name=a.get("name", ""),
                repo=a.get("repo", ""),
                branch=a.get("branch", ""),
                branches=a.get("branches", []),
            )
            for a in data.get("apps", [])
        ]
        mariadb = MariaDBConfig(**data.get("mariadb", {}))
        postgres = PostgresConfig(**data.get("postgres", {}))
        redis = cls._parse_redis(data.get("redis", {}))
        workers = cls._parse_workers(data.get("workers", []))
        production = cls._parse_production(data.get("production"))
        monitor = cls._parse_monitor(data.get("monitor", {}))
        gunicorn = cls._parse_gunicorn(data.get("gunicorn", {}), bench_data.get("http_port", 8000))
        letsencrypt = cls._parse_letsencrypt(data.get("letsencrypt", {}))
        admin = cls._parse_admin(data.get("admin", {}))
        central = cls._parse_central(data.get("central", {}))
        firewall = cls._parse_firewall(data.get("firewall"))
        s3 = S3Config(**data.get("s3", {}))
        return cls(
            name=bench_data.get("name", ""),
            python_version=bench_data.get("python", ""),
            http_port=bench_data.get("http_port", 8000),
            socketio_port=bench_data.get("socketio_port", 9000),
            socketio_backend=bench_data.get("socketio_backend", "node"),
            watch_apps_js=bench_data.get("watch_apps_js", False),
            reload_python=bench_data.get("reload_python", False),
            watch_admin_js=bench_data.get("watch_admin_js", False),
            db_type=bench_data.get("db_type", "mariadb"),
            default_branch=bench_data.get("default_branch", ""),
            apps=apps,
            mariadb=mariadb,
            postgres=postgres,
            redis=redis,
            workers=workers,
            production=production,
            monitor=monitor,
            gunicorn=gunicorn,
            letsencrypt=letsencrypt,
            admin=admin,
            central=central,
            firewall=firewall,
            s3=s3,
        )

    @staticmethod
    def _parse_redis(data: dict) -> RedisConfig:
        return RedisConfig(
            cache_port=data.get("cache_port", 13000),
            queue_port=data.get("queue_port", 11000),
            version=data.get("version"),
        )

    @staticmethod
    def _parse_workers(data: list) -> WorkerConfig:
        # [[workers]] array-of-tables: each group lists queues and a count.
        if not isinstance(data, list) or not data:
            return WorkerConfig()
        groups = [
            WorkerGroup(
                queues=entry.get("queues", [entry.get("queue", "default")]),
                count=entry.get("count", 1),
            )
            for entry in data
        ]
        return WorkerConfig(groups=groups)

    @staticmethod
    def _normalize_process_manager(value: str) -> str:
        v = (value or "").strip().lower()
        if v in ("", "none"):
            return ""
        if v == "supervisord":
            return "supervisor"
        return v

    @staticmethod
    def _parse_production(data: dict | None) -> ProductionConfig:
        if data is None:
            return ProductionConfig()
        pm = BenchConfig._normalize_process_manager(str(data.get("process_manager", "")))
        if "enabled" in data:
            enabled = bool(data.get("enabled"))
        else:
            # Legacy: presence of a real process_manager implied production.
            enabled = pm != ""
        # Oldest format derived the manager from a `lightweight` flag.
        if enabled and not pm and "lightweight" in data:
            pm = "systemd" if data.get("lightweight", False) else "supervisor"
        return ProductionConfig(
            enabled=enabled,
            process_manager=pm,
            use_companion_manager=data.get("use_companion_manager", False),
        )

    @staticmethod
    def _parse_monitor(data: dict) -> MonitorConfig:
        return MonitorConfig(
            system_log_path=Path(data.get("system_log_path", "/var/log/bench-system-stats.log")),
            authority_file_path=Path(data.get("authority_file_path", "/var/log/.bench-authority")),
            system_log_max_size=data.get("system_log_max_size", "500M"),
            application_log_max_size=data.get("application_log_max_size", "500M"),
            log_path=Path(data["log_path"]) if "log_path" in data else None,
        )

    @staticmethod
    def _parse_gunicorn(data: dict, http_port: int = 8000) -> GunicornConfig:
        d = GunicornConfig()
        return GunicornConfig(
            workers=data.get("workers", d.workers),
            threads=data.get("threads", d.threads),
            timeout=data.get("timeout", d.timeout),
            worker_class=data.get("worker_class", d.worker_class),
            malloc_arena_max=data.get("malloc_arena_max", d.malloc_arena_max),
            max_requests=data.get("max_requests", d.max_requests),
            max_requests_jitter=data.get("max_requests_jitter", d.max_requests_jitter),
        )

    @staticmethod
    def _parse_letsencrypt(data: dict) -> LetsEncryptConfig:
        webroot_path = data.get("webroot_path", "/var/www/letsencrypt")
        return LetsEncryptConfig(
            email=data.get("email", ""),
            webroot_path=Path(webroot_path),
        )

    @staticmethod
    def _parse_admin(data: dict) -> AdminConfig:
        return AdminConfig(
            port=data.get("port", 7000),
            timeout=data.get("timeout", 180),
            enabled=data.get("enabled", False),
            password=data.get("password", ""),
            jwt_secret=data.get("jwt_secret", ""),
            jwks_url=data.get("jwks_url", ""),
            jwks_audience=data.get("jwks_audience", ""),
            domain=data.get("domain", ""),
            tls=data.get("tls", False),
            allow_bench_management=data.get("allow_bench_management", True),
        )

    @staticmethod
    def _parse_central(data: dict) -> CentralConfig:
        return CentralConfig(
            endpoint=data.get("endpoint", ""),
            auth_token=data.get("auth_token", ""),
        )

    @staticmethod
    def _parse_firewall(data: dict | None) -> FirewallConfig:
        if not data:
            return FirewallConfig()
        rules = [
            FirewallRule(
                ip=str(rule.get("ip", "")),
                action=str(rule.get("action", "deny")),
                description=str(rule.get("description", "")),
            )
            for rule in data.get("rules", [])
        ]
        return FirewallConfig(
            enabled=bool(data.get("enabled", False)),
            default=str(data.get("default", "allow")),
            rules=rules,
        )

    def validate(self) -> None:
        self._validate_required_fields()
        self._validate_bench_name()
        self._validate_app_names_unique()
        self._validate_ports()
        self._validate_socketio_backend()
        self._validate_db_type()
        self._validate_redis_ports()
        self._validate_worker_counts()
        self._validate_letsencrypt_email()
        self._validate_gunicorn()
        self._validate_mariadb_version()
        self._validate_mariadb_instance()
        self._validate_postgres_instance()
        self._validate_redis_version()
        self._validate_production()
        self._validate_admin_domain()
        self._validate_firewall()

    def _validate_required_fields(self) -> None:
        if not self.name:
            raise ConfigError("bench.name is required and must not be empty.")
        if not self.python_version:
            raise ConfigError("bench.python is required and must not be empty.")
        for app in self.apps:
            if not app.name or not app.repo or not app.branch:
                raise ConfigError(f"App '{app.name or '(unnamed)'}' must have name, repo, and branch.")
            if app.branches and app.branch not in app.branches:
                raise ConfigError(f"App '{app.name}': active branch '{app.branch}' is not listed in branches {app.branches}.")

    def _validate_bench_name(self) -> None:
        if not _BENCH_NAME_PATTERN.match(self.name):
            raise ConfigError(
                f"bench.name '{self.name}' is invalid. Must start with a letter and contain only letters, digits, underscores, or hyphens."
            )

    def _validate_app_names_unique(self) -> None:
        names = [app.name for app in self.apps]
        seen = set()
        for name in names:
            if name in seen:
                raise ConfigError(f"Duplicate app name '{name}'. App names must be unique.")
            seen.add(name)

    def _validate_ports(self) -> None:
        ports = {
            "bench.http_port": self.http_port,
            "bench.socketio_port": self.socketio_port,
            "mariadb.port": self.mariadb.port,
            "postgres.port": self.postgres.port,
        }
        for name, port in ports.items():
            if not (_PORT_MIN <= port <= _PORT_MAX):
                raise ConfigError(f"{name} {port} is out of range. Must be between {_PORT_MIN} and {_PORT_MAX}.")

    def _validate_socketio_backend(self) -> None:
        if self.socketio_backend not in ("python", "node"):
            raise ConfigError(f"bench.socketio_backend '{self.socketio_backend}' is invalid. Must be 'python' or 'node'.")

    def _validate_db_type(self) -> None:
        if self.db_type not in ("mariadb", "postgres", "sqlite"):
            raise ConfigError(f"bench.db_type '{self.db_type}' is invalid. Must be 'mariadb', 'postgres', or 'sqlite'.")

    def _validate_redis_ports(self) -> None:
        ports = [self.redis.cache_port, self.redis.queue_port]
        port_names = ["redis.cache_port", "redis.queue_port"]
        for name, port in zip(port_names, ports):
            if not (_REDIS_PORT_MIN <= port <= _REDIS_PORT_MAX):
                raise ConfigError(f"{name} {port} is out of range. Must be between {_REDIS_PORT_MIN} and {_REDIS_PORT_MAX}.")

        if self.redis.cache_port == self.redis.queue_port:
            raise ConfigError(f"redis.cache_port and redis.queue_port must be distinct, but both are set to {self.redis.cache_port}.")

    def _validate_worker_counts(self) -> None:
        if not self.workers.groups:
            raise ConfigError("workers.groups must contain at least one worker group.")
        for i, group in enumerate(self.workers.groups):
            prefix = f"workers[{i}]"
            if not isinstance(group.queues, list) or not group.queues:
                raise ConfigError(f"{prefix}.queues must be a non-empty list.")
            if not all(isinstance(q, str) and q for q in group.queues):
                raise ConfigError(f"{prefix}.queues must contain non-empty strings.")
            if not isinstance(group.count, int) or group.count < 1:
                raise ConfigError(f"{prefix}.count must be a positive integer, got '{group.count}'.")

    def _validate_letsencrypt_email(self) -> None:
        if self.letsencrypt.email and not _EMAIL_PATTERN.match(self.letsencrypt.email):
            raise ConfigError(f"letsencrypt.email '{self.letsencrypt.email}' is not a valid email address.")

    def _validate_production(self) -> None:
        from pilot.config.production_config import VALID_PROCESS_MANAGERS

        pm = self.production.process_manager
        if self.production.enabled:
            if pm not in VALID_PROCESS_MANAGERS:
                raise ConfigError(
                    f"production.process_manager must be one of {', '.join(VALID_PROCESS_MANAGERS)} "
                    f"when production is enabled (bench '{self.name}'), got '{pm or '(empty)'}'."
                )
        elif pm and pm not in VALID_PROCESS_MANAGERS:
            raise ConfigError(
                f"production.process_manager '{pm}' is invalid (bench '{self.name}'). Must be one of {', '.join(VALID_PROCESS_MANAGERS)}."
            )

    def _validate_admin_domain(self) -> None:
        domain = self.admin.domain
        if not domain:
            if self.production.enabled:
                raise ConfigError(
                    f"admin.domain is required in production but is missing for bench '{self.name}'. "
                    f"Set it in bench.toml (e.g. admin.example.com) or pass "
                    f"'bench setup production --admin-domain <domain>'."
                )
            return
        if not _HOSTNAME_PATTERN.match(domain):
            raise ConfigError(f"admin.domain '{domain}' is not a valid hostname (bench '{self.name}').")

    def _validate_firewall(self) -> None:
        import ipaddress

        fw = self.firewall
        if fw.default not in ("allow", "deny"):
            raise ConfigError(f"firewall.default '{fw.default}' is invalid. Must be 'allow' or 'deny'.")
        for i, rule in enumerate(fw.rules):
            prefix = f"firewall.rules[{i}]"
            if rule.action not in ("allow", "deny"):
                raise ConfigError(f"{prefix}.action '{rule.action}' is invalid. Must be 'allow' or 'deny'.")
            try:
                # strict=False accepts a host address with a prefix (e.g. 10.0.0.5/8).
                ipaddress.ip_network(rule.ip, strict=False)
            except ValueError:
                raise ConfigError(f"{prefix}.ip '{rule.ip}' is not a valid IPv4/IPv6 address or CIDR range.")

    def _validate_gunicorn(self) -> None:
        if not isinstance(self.gunicorn.workers, int) or self.gunicorn.workers < 1:
            raise ConfigError(f"gunicorn.workers must be a positive integer, got '{self.gunicorn.workers}'.")
        if not isinstance(self.gunicorn.threads, int) or self.gunicorn.threads < 1:
            raise ConfigError(f"gunicorn.threads must be a positive integer, got '{self.gunicorn.threads}'.")
        if not isinstance(self.gunicorn.timeout, int) or self.gunicorn.timeout < 1:
            raise ConfigError(f"gunicorn.timeout must be a positive integer, got '{self.gunicorn.timeout}'.")
        if not self.gunicorn.worker_class:
            raise ConfigError("gunicorn.worker_class must not be empty.")
        if not isinstance(self.gunicorn.malloc_arena_max, int) or self.gunicorn.malloc_arena_max < 0:
            raise ConfigError(f"gunicorn.malloc_arena_max must be a non-negative integer, got '{self.gunicorn.malloc_arena_max}'.")
        if not isinstance(self.gunicorn.max_requests, int) or self.gunicorn.max_requests < 0:
            raise ConfigError(f"gunicorn.max_requests must be a non-negative integer, got '{self.gunicorn.max_requests}'.")
        if not isinstance(self.gunicorn.max_requests_jitter, int) or self.gunicorn.max_requests_jitter < 0:
            raise ConfigError(f"gunicorn.max_requests_jitter must be a non-negative integer, got '{self.gunicorn.max_requests_jitter}'.")

    def _validate_mariadb_version(self) -> None:
        if self.mariadb.version and not _VERSION_PATTERN.match(self.mariadb.version):
            raise ConfigError(f"mariadb.version '{self.mariadb.version}' is invalid. Must be a version string like '11.8' or '11.4'.")

    def _validate_mariadb_instance(self) -> None:
        instance = self.mariadb.instance
        if instance and not _BENCH_NAME_PATTERN.match(instance):
            raise ConfigError(
                f"mariadb.instance '{instance}' is invalid. Must start with a letter and contain only letters, digits, underscores, or hyphens."
            )
        if self.mariadb.data_dir and not Path(self.mariadb.data_dir).is_absolute():
            raise ConfigError(f"mariadb.data_dir '{self.mariadb.data_dir}' must be an absolute path.")

    def _validate_postgres_instance(self) -> None:
        instance = self.postgres.instance
        if instance and not _BENCH_NAME_PATTERN.match(instance):
            raise ConfigError(
                f"postgres.instance '{instance}' is invalid. Must start with a letter and contain only letters, digits, underscores, or hyphens."
            )

    def _validate_redis_version(self) -> None:
        if self.redis.version and not _VERSION_PATTERN.match(self.redis.version):
            raise ConfigError(f"redis.version '{self.redis.version}' is invalid. Must be a version string like '7' or '7.0'.")

    @property
    def framework_app(self) -> AppConfig:
        if not self.apps:
            return AppConfig(name="frappe", repo="", branch="")
        return self.apps[0]

    def app_by_name(self, name: str) -> AppConfig:
        for app in self.apps:
            if app.name == name:
                return app
        raise KeyError(f"No app named '{name}' found in config.")
