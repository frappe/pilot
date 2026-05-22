import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml

from bench2.config.admin_config import AdminConfig
from bench2.config.app_config import AppConfig
from bench2.config.letsencrypt_config import LetsEncryptConfig
from bench2.config.mariadb_config import MariaDBConfig
from bench2.config.nginx_config import NginxConfig
from bench2.config.redis_config import RedisConfig
from bench2.config.site_config import SiteConfig
from bench2.config.worker_config import WorkerConfig
from bench2.exceptions import ConfigError

_BENCH_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
_EMAIL_PATTERN = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
_VALID_PROCESS_MANAGERS = {"honcho", "supervisor"}
_REDIS_PORT_MIN = 1024
_REDIS_PORT_MAX = 65535


@dataclass
class BenchConfig:
    name: str
    python_version: str
    process_manager: str
    apps: List[AppConfig]
    sites: List[SiteConfig]
    mariadb: MariaDBConfig
    redis: RedisConfig
    workers: WorkerConfig
    http_port: int = 8000
    socketio_port: int = 9000
    nginx: NginxConfig = field(default_factory=NginxConfig)
    letsencrypt: LetsEncryptConfig = field(default_factory=LetsEncryptConfig)
    admin: AdminConfig = field(default_factory=AdminConfig)

    @classmethod
    def from_file(cls, path: Path) -> "BenchConfig":
        with path.open() as file_handle:
            data = yaml.safe_load(file_handle)
        config = cls._from_dict(data)
        config.validate()
        return config

    @classmethod
    def _from_dict(cls, data: dict) -> "BenchConfig":
        bench_data = data.get("bench", {})
        apps = [AppConfig(**app) for app in data.get("apps", [])]
        sites = [cls._parse_site(site) for site in data.get("sites", [])]
        mariadb = MariaDBConfig(**data.get("mariadb", {}))
        redis = RedisConfig(**data.get("redis", {}))
        workers = cls._parse_workers(data.get("workers", {}))
        nginx = cls._parse_nginx(data.get("nginx", {}))
        letsencrypt = cls._parse_letsencrypt(data.get("letsencrypt", {}))
        admin = cls._parse_admin(data.get("admin", {}))
        return cls(
            name=bench_data.get("name", ""),
            python_version=bench_data.get("python", ""),
            process_manager=bench_data.get("process_manager", "honcho"),
            http_port=bench_data.get("http_port", 8000),
            socketio_port=bench_data.get("socketio_port", 9000),
            apps=apps,
            sites=sites,
            mariadb=mariadb,
            redis=redis,
            workers=workers,
            nginx=nginx,
            letsencrypt=letsencrypt,
            admin=admin,
        )

    @staticmethod
    def _parse_site(data: dict) -> SiteConfig:
        return SiteConfig(
            name=data.get("name", ""),
            apps=data.get("apps", []),
            admin_password=data.get("admin_password", "admin"),
            domains=data.get("domains", []),
            ssl=data.get("ssl", False),
        )

    @staticmethod
    def _parse_workers(data: dict) -> WorkerConfig:
        return WorkerConfig(
            default_count=data.get("default", 2),
            short_count=data.get("short", 1),
            long_count=data.get("long", 1),
        )

    @staticmethod
    def _parse_nginx(data: dict) -> NginxConfig:
        config_dir = data.get("config_dir", "/etc/nginx/conf.d")
        return NginxConfig(
            enabled=data.get("enabled", False),
            http_port=data.get("http_port", 80),
            https_port=data.get("https_port", 443),
            config_dir=Path(config_dir),
            worker_processes=str(data.get("worker_processes", "auto")),
            client_max_body_size=data.get("client_max_body_size", "50m"),
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
            port=data.get("port", 8002),
            timeout=data.get("timeout", 900),
        )

    def validate(self) -> None:
        self._validate_required_fields()
        self._validate_bench_name()
        self._validate_process_manager()
        self._validate_app_names_unique()
        self._validate_site_names_unique()
        self._validate_site_apps_reference_known_apps()
        self._validate_site_apps_start_with_framework()
        self._validate_redis_ports()
        self._validate_worker_counts()
        self._validate_ssl_requirements()
        self._validate_letsencrypt_email()
        self._validate_site_domains()
        self._validate_nginx_ports_distinct()

    def _validate_required_fields(self) -> None:
        if not self.name:
            raise ConfigError("bench.name is required and must not be empty.")
        if not self.python_version:
            raise ConfigError("bench.python is required and must not be empty.")
        if not self.apps:
            raise ConfigError("At least one app must be defined under apps.")
        for app in self.apps:
            if not app.name or not app.repo or not app.branch:
                raise ConfigError(
                    f"App '{app.name or '(unnamed)'}' must have name, repo, and branch."
                )
        if not self.sites:
            raise ConfigError("At least one site must be defined under sites.")
        for site in self.sites:
            if not site.name or not site.apps:
                raise ConfigError(
                    f"Site '{site.name or '(unnamed)'}' must have name and apps."
                )

    def _validate_bench_name(self) -> None:
        if not _BENCH_NAME_PATTERN.match(self.name):
            raise ConfigError(
                f"bench.name '{self.name}' is invalid. "
                "Must start with a letter and contain only letters, digits, underscores, or hyphens."
            )

    def _validate_process_manager(self) -> None:
        if self.process_manager not in _VALID_PROCESS_MANAGERS:
            raise ConfigError(
                f"bench.process_manager '{self.process_manager}' is invalid. "
                f"Must be one of: {', '.join(sorted(_VALID_PROCESS_MANAGERS))}."
            )

    def _validate_app_names_unique(self) -> None:
        names = [app.name for app in self.apps]
        seen = set()
        for name in names:
            if name in seen:
                raise ConfigError(f"apps[].name '{name}' appears more than once. App names must be unique.")
            seen.add(name)

    def _validate_site_names_unique(self) -> None:
        names = [site.name for site in self.sites]
        seen = set()
        for name in names:
            if name in seen:
                raise ConfigError(f"sites[].name '{name}' appears more than once. Site names must be unique.")
            seen.add(name)

    def _validate_site_apps_reference_known_apps(self) -> None:
        known_app_names = {app.name for app in self.apps}
        for site in self.sites:
            for app_name in site.apps:
                if app_name not in known_app_names:
                    raise ConfigError(
                        f"Site '{site.name}' references app '{app_name}' which is not defined in apps[]."
                    )

    def _validate_site_apps_start_with_framework(self) -> None:
        framework_name = self.framework_app.name
        for site in self.sites:
            if not site.apps or site.apps[0] != framework_name:
                raise ConfigError(
                    f"Site '{site.name}' apps list must begin with the framework app '{framework_name}'."
                )

    def _validate_redis_ports(self) -> None:
        ports = [self.redis.cache_port, self.redis.queue_port, self.redis.socketio_port]
        port_names = ["redis.cache_port", "redis.queue_port", "redis.socketio_port"]
        for name, port in zip(port_names, ports):
            if not (_REDIS_PORT_MIN <= port <= _REDIS_PORT_MAX):
                raise ConfigError(
                    f"{name} {port} is out of range. Must be between {_REDIS_PORT_MIN} and {_REDIS_PORT_MAX}."
                )
        if len(set(ports)) != len(ports):
            raise ConfigError("redis.cache_port, redis.queue_port, and redis.socketio_port must all be distinct.")

    def _validate_worker_counts(self) -> None:
        counts = {
            "workers.default_count": self.workers.default_count,
            "workers.short_count": self.workers.short_count,
            "workers.long_count": self.workers.long_count,
        }
        for name, count in counts.items():
            if not isinstance(count, int) or count < 1:
                raise ConfigError(f"{name} must be a positive integer, got '{count}'.")

    def _validate_ssl_requirements(self) -> None:
        ssl_sites = [site for site in self.sites if site.ssl]
        if not ssl_sites:
            return
        if not self.nginx.enabled:
            raise ConfigError(
                "nginx.enabled must be true when any site has ssl: true."
            )
        if not self.letsencrypt.email:
            raise ConfigError(
                "letsencrypt.email must be set when any site has ssl: true."
            )

    def _validate_letsencrypt_email(self) -> None:
        if self.letsencrypt.email and not _EMAIL_PATTERN.match(self.letsencrypt.email):
            raise ConfigError(
                f"letsencrypt.email '{self.letsencrypt.email}' is not a valid email address."
            )

    def _validate_site_domains(self) -> None:
        for site in self.sites:
            for domain in site.domains:
                if " " in domain or "/" in domain or "\\" in domain:
                    raise ConfigError(
                        f"Site '{site.name}' has an invalid domain '{domain}'. "
                        "Domains must not contain spaces or path separators."
                    )

    def _validate_nginx_ports_distinct(self) -> None:
        if self.nginx.http_port == self.nginx.https_port:
            raise ConfigError(
                f"nginx.http_port and nginx.https_port must be distinct, "
                f"but both are set to {self.nginx.http_port}."
            )

    def app_by_name(self, name: str) -> AppConfig:
        for app in self.apps:
            if app.name == name:
                return app
        raise KeyError(f"No app named '{name}' found in config.")

    @property
    def framework_app(self) -> AppConfig:
        return self.apps[0]
