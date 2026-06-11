import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from bench_cli.config.admin_config import AdminConfig
from bench_cli.config.app_config import AppConfig
from bench_cli.config.letsencrypt_config import LetsEncryptConfig
from bench_cli.config.mariadb_config import MariaDBConfig
from bench_cli.config.nginx_config import NginxConfig
from bench_cli.config.production_config import ProductionConfig
from bench_cli.config.redis_config import RedisConfig
from bench_cli.config.volume_config import BenchesDatasetConfig, ImageConfig, MariaDBDatasetConfig, VolumeConfig
from bench_cli.config.worker_config import CustomWorkerEntry, WorkerConfig
from bench_cli.exceptions import ConfigError

_BENCH_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
_EMAIL_PATTERN = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
_VERSION_PATTERN = re.compile(r"^\d+(\.\d+)*$")
_ZFS_SIZE_PATTERN = re.compile(r"^[1-9]\d*[KMGTkmgt]?$")
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
    apps: List[AppConfig] = field(default_factory=list)
    http_port: int = 8000
    socketio_port: int = 9000
    production: ProductionConfig = field(default_factory=ProductionConfig)
    nginx: NginxConfig = field(default_factory=NginxConfig)
    letsencrypt: LetsEncryptConfig = field(default_factory=LetsEncryptConfig)
    admin: AdminConfig = field(default_factory=AdminConfig)
    volume: VolumeConfig = field(default_factory=VolumeConfig)

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
        redis = cls._parse_redis(data.get("redis", {}))
        workers = cls._parse_workers(data.get("workers", {}))
        production = cls._parse_production(data.get("production"))
        nginx = cls._parse_nginx(data.get("nginx", {}))
        letsencrypt = cls._parse_letsencrypt(data.get("letsencrypt", {}))
        admin = cls._parse_admin(data.get("admin", {}))
        volume = cls._parse_volume(data.get("volume", {}))
        return cls(
            name=bench_data.get("name", ""),
            python_version=bench_data.get("python", ""),
            http_port=bench_data.get("http_port", 8000),
            socketio_port=bench_data.get("socketio_port", 9000),
            apps=apps,
            mariadb=mariadb,
            redis=redis,
            workers=workers,
            production=production,
            nginx=nginx,
            letsencrypt=letsencrypt,
            admin=admin,
            volume=volume,
        )

    @staticmethod
    def _parse_redis(data: dict) -> RedisConfig:
        if "port" in data:
            port = data["port"]
            return RedisConfig(
                cache_port=port,
                queue_port=port,
                socketio_port=port,
                version=data.get("version"),
            )
        return RedisConfig(
            cache_port=data.get("cache_port", 13000),
            queue_port=data.get("queue_port", 11000),
            socketio_port=data.get("socketio_port", 12000),
            version=data.get("version"),
        )

    @staticmethod
    def _parse_workers(data: dict) -> WorkerConfig:
        custom = [
            CustomWorkerEntry(
                queue=entry["queue"],
                count=entry.get("count", 1),
                timeout=entry.get("timeout", 300),
            )
            for entry in data.get("custom", [])
        ]
        return WorkerConfig(
            default_count=data.get("default", 2),
            short_count=data.get("short", 1),
            long_count=data.get("long", 1),
            custom=custom,
        )

    @staticmethod
    def _parse_production(data: dict | None) -> ProductionConfig:
        if data is None:
            return ProductionConfig()
        if "process_manager" in data:
            return ProductionConfig(
                process_manager=str(data.get("process_manager", "none")),
                nginx=data.get("nginx", False),
            )
        # Legacy format: enabled + lightweight → derive process_manager
        if data.get("enabled", False):
            pm = "systemd" if data.get("lightweight", False) else "supervisor"
        else:
            pm = "none"
        return ProductionConfig(process_manager=pm, nginx=data.get("nginx", False))

    @staticmethod
    def _parse_nginx(data: dict) -> NginxConfig:
        config_dir = data.get("config_dir", "/etc/nginx/conf.d")
        return NginxConfig(
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
            timeout=data.get("timeout", 180),
            enabled=data.get("enabled", False),
            password=data.get("password", ""),
            domain=data.get("domain", ""),
        )

    @staticmethod
    def _parse_volume(data: dict) -> VolumeConfig:
        benches_data = data.get("benches", {})
        mariadb_data = data.get("mariadb", {})
        image_data = data.get("image", {})
        # Older tomls predate `backing`: an explicit device implies device backing.
        backing = data.get("backing") or ("device" if data.get("device") else "auto")
        return VolumeConfig(
            pool=data.get("pool", "bench-pool"),
            backing=backing,
            device=data.get("device", ""),
            image=ImageConfig(
                size=image_data.get("size", ""),
                path=image_data.get("path", ""),
            ),
            benches=BenchesDatasetConfig(
                reservation=benches_data.get("reservation", "10G"),
                quota=benches_data.get("quota", "50G"),
                data_dir=benches_data.get("data_dir", "/home/frappe/bench"),
            ),
            mariadb=MariaDBDatasetConfig(
                reservation=mariadb_data.get("reservation", "5G"),
                quota=mariadb_data.get("quota", "20G"),
                data_dir=mariadb_data.get("data_dir", "/var/lib/mysql"),
            ),
        )

    def validate(self) -> None:
        self._validate_required_fields()
        self._validate_bench_name()
        self._validate_app_names_unique()
        self._validate_ports()
        self._validate_redis_ports()
        self._validate_worker_counts()
        self._validate_letsencrypt_email()
        self._validate_nginx_ports_distinct()
        self._validate_mariadb_version()
        self._validate_redis_version()
        self._validate_volume()

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
            raise ConfigError(f"bench.name '{self.name}' is invalid. Must start with a letter and contain only letters, digits, underscores, or hyphens.")

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
            "nginx.http_port": self.nginx.http_port,
            "nginx.https_port": self.nginx.https_port,
        }
        for name, port in ports.items():
            if not (_PORT_MIN <= port <= _PORT_MAX):
                raise ConfigError(f"{name} {port} is out of range. Must be between {_PORT_MIN} and {_PORT_MAX}.")

    def _validate_redis_ports(self) -> None:
        ports = [self.redis.cache_port, self.redis.queue_port, self.redis.socketio_port]
        port_names = ["redis.cache_port", "redis.queue_port", "redis.socketio_port"]
        for name, port in zip(port_names, ports):
            if not (_REDIS_PORT_MIN <= port <= _REDIS_PORT_MAX):
                raise ConfigError(f"{name} {port} is out of range. Must be between {_REDIS_PORT_MIN} and {_REDIS_PORT_MAX}.")

    def _validate_worker_counts(self) -> None:
        counts = {
            "workers.default_count": self.workers.default_count,
            "workers.short_count": self.workers.short_count,
            "workers.long_count": self.workers.long_count,
        }
        for name, count in counts.items():
            if not isinstance(count, int) or count < 1:
                raise ConfigError(f"{name} must be a positive integer, got '{count}'.")
        for entry in self.workers.custom:
            if not isinstance(entry.count, int) or entry.count < 1:
                raise ConfigError(f"workers.custom '{entry.queue}' count must be a positive integer, got '{entry.count}'.")

    def _validate_letsencrypt_email(self) -> None:
        if self.letsencrypt.email and not _EMAIL_PATTERN.match(self.letsencrypt.email):
            raise ConfigError(f"letsencrypt.email '{self.letsencrypt.email}' is not a valid email address.")

    def _validate_nginx_ports_distinct(self) -> None:
        if self.nginx.http_port == self.nginx.https_port:
            raise ConfigError(f"nginx.http_port and nginx.https_port must be distinct, but both are set to {self.nginx.http_port}.")

    def _validate_mariadb_version(self) -> None:
        if self.mariadb.version and not _VERSION_PATTERN.match(self.mariadb.version):
            raise ConfigError(f"mariadb.version '{self.mariadb.version}' is invalid. Must be a version string like '10.6' or '11.4'.")

    def _validate_redis_version(self) -> None:
        if self.redis.version and not _VERSION_PATTERN.match(self.redis.version):
            raise ConfigError(f"redis.version '{self.redis.version}' is invalid. Must be a version string like '7' or '7.0'.")

    def _validate_volume(self) -> None:
        if not self.volume.pool:
            raise ConfigError("volume.pool is required.")
        self._validate_volume_backing()
        self._validate_zfs_size("volume.benches.reservation", self.volume.benches.reservation)
        self._validate_zfs_size("volume.benches.quota", self.volume.benches.quota)
        self._validate_zfs_size("volume.mariadb.reservation", self.volume.mariadb.reservation)
        self._validate_zfs_size("volume.mariadb.quota", self.volume.mariadb.quota)
        self._validate_reservation_quota()
        if not Path(self.volume.mariadb.data_dir).is_absolute():
            raise ConfigError(f"volume.mariadb.data_dir '{self.volume.mariadb.data_dir}' must be an absolute path.")

    def _validate_volume_backing(self) -> None:
        backing = self.volume.backing
        if backing not in ("device", "image", "auto"):
            raise ConfigError(f"volume.backing '{backing}' is invalid. Must be 'device', 'image', or 'auto'.")
        if backing == "auto":
            # Resolved to a concrete backing (with smart sizes) during bench init.
            return
        if backing == "device":
            if not self.volume.device:
                raise ConfigError("volume.device is required when volume.backing = 'device'.")
            return
        if not self.volume.image.size:
            raise ConfigError("volume.image.size is required when volume.backing = 'image'.")
        self._validate_zfs_size("volume.image.size", self.volume.image.size)
        if self.volume.image.path and not Path(self.volume.image.path).is_absolute():
            raise ConfigError(f"volume.image.path '{self.volume.image.path}' must be an absolute path.")

    def _validate_reservation_quota(self) -> None:
        from bench_cli.managers.volume_manager import VolumeManager

        for label, dataset in (("benches", self.volume.benches), ("mariadb", self.volume.mariadb)):
            if error := VolumeManager.validate_reservation_within_quota(dataset.reservation, dataset.quota, label):
                raise ConfigError(error)

    @staticmethod
    def _validate_zfs_size(field_name: str, value: str) -> None:
        if not _ZFS_SIZE_PATTERN.match(value):
            raise ConfigError(
                f"{field_name} '{value}' is not a valid ZFS size. Must be a positive integer with an "
                f"optional K/M/G/T suffix — examples: '10G', '512M', '1T'. Decimals and negatives are not allowed."
            )

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
