from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pilot.utils import write_private_text

if TYPE_CHECKING:
    from pilot.config import S3Config
    from pilot.core.bench import Bench


class BenchConfigFiles:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    @property
    def db_root_args(self) -> list[str]:
        if self.bench.config.db_type == "postgres":
            postgres = self.bench.config.postgres
            return [
                "--db-root-username",
                postgres.admin_user,
                "--db-root-password",
                self.postgres_root_password,
            ]
        if self.bench.config.db_type == "sqlite":
            return []
        mariadb = self.bench.config.mariadb
        return [
            "--db-root-username",
            mariadb.admin_user,
            "--db-root-password",
            mariadb.root_password,
        ]

    @property
    def postgres_root_password(self) -> str:
        return self.bench.config.postgres.root_password or "trust_auth"

    def set_maintenance_mode(self, enabled: bool) -> None:
        config_path = self.bench.sites_path / "common_site_config.json"
        config = json.loads(config_path.read_text())
        config["maintenance_mode"] = 1 if enabled else 0
        write_private_text(config_path, json.dumps(config, indent=2))

    def set_developer_mode(self, enabled: bool) -> None:
        config_path = self.bench.sites_path / "common_site_config.json"
        if not config_path.exists():
            return  # written with the right value on the next write_common_site_config
        config = json.loads(config_path.read_text())
        value = 1 if enabled else 0
        if config.get("developer_mode") == value:
            return
        config["developer_mode"] = value
        write_private_text(config_path, json.dumps(config, indent=2) + "\n")

    def sync_s3_credentials(self, s3_config: "S3Config") -> None:
        config_path = self.bench.sites_path / "common_site_config.json"
        if not config_path.exists():
            return

        config = json.loads(config_path.read_text())
        config["s3_access_key"] = s3_config.access_key
        config["s3_bucket"] = s3_config.bucket
        config["s3_secret_key"] = s3_config.secret_key
        config["s3_provider"] = s3_config.provider
        config["s3_region"] = s3_config.region
        write_private_text(config_path, json.dumps(config, indent=2) + "\n")

    def write_common_site_config(self) -> None:
        redis = self.bench.config.redis
        redis_cache = f"redis://localhost:{redis.cache_port}"
        config_path = self.bench.sites_path / "common_site_config.json"
        try:
            config = json.loads(config_path.read_text()) if config_path.exists() else {}
        except json.JSONDecodeError:
            config = {}
        config.update(
            {
                "redis_cache": redis_cache,
                "redis_queue": f"redis://localhost:{redis.queue_port}",
                "redis_socketio": redis_cache,
                "socketio_port": self.bench.config.socketio_port,
                "webserver_port": self.bench.config.http_port,
                "socketio_backend": self.bench.config.socketio_backend,
                "monitor": True,
                "developer_mode": 1 if self.bench.config.developer_mode else 0,
            }
        )
        write_private_text(config_path, json.dumps(config, indent=2) + "\n")
