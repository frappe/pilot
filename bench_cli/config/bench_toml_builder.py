from __future__ import annotations

import tomllib
from pathlib import Path

_BASE_TEMPLATE = """\
[bench]
name = "{name}"
python = "{python}"
http_port = {http_port}
socketio_port = {socketio_port}
default_branch = "{default_branch}"

[[apps]]
name = "frappe"
repo = "{app_repo}"
branch = "{default_branch}"

[mariadb]
host = "localhost"
port = 3306
root_password = "{mariadb_password}"

[redis]
port = {redis_port}

[workers]
default = {workers_default}
short = {workers_short}
long = {workers_long}

[admin]
port = {admin_port}
enabled = {admin_enabled}
timeout = 180
password = "{admin_password}"
"""

_VOLUME_TEMPLATE = """
[volume]
enabled = true
pool = "{volume_pool}"
device = "{volume_device}"

[volume.benches]
reservation = "{volume_benches_reservation}"
quota = "{volume_benches_quota}"

[volume.mariadb]
reservation = "{volume_mariadb_reservation}"
quota = "{volume_mariadb_quota}"
data_dir = "{volume_mariadb_data_dir}"

[volume.snapshots]
enabled = {volume_snapshots_enabled}
"""


def _toml_bool(value: object) -> str:
    return "true" if value else "false"


class BenchTomlBuilder:
    """Single source of truth for bench settings: defaults, rendering, and reading.

    All three operations share the same key names so adding a field only
    requires touching this class and the wizard frontend.
    """

    DEFAULTS: dict = {
        "python": "3.14",
        "http_port": 8000,
        "socketio_port": 9000,
        "app_repo": "https://github.com/frappe/frappe",
        "default_branch": "version-16",
        "mariadb_password": "root",
        "admin_password": "",
        "admin_port": 8002,
        "admin_enabled": False,
        "redis_port": 13000,
        "workers_default": 2,
        "workers_short": 1,
        "workers_long": 1,
        "volume_enabled": False,
        "volume_pool": "",
        "volume_device": "",
        "volume_benches_reservation": "10G",
        "volume_benches_quota": "50G",
        "volume_mariadb_reservation": "5G",
        "volume_mariadb_quota": "20G",
        "volume_mariadb_data_dir": "/var/lib/mysql",
        "volume_snapshots_enabled": False,
    }

    def __init__(self, name: str, settings: dict | None = None) -> None:
        self._name = name
        self._settings = {**self.DEFAULTS, **(settings or {})}

    def render(self) -> str:
        content = self._render_base()
        if self._settings.get("volume_enabled"):
            content += self._render_volume()
        return content

    def _render_base(self) -> str:
        return _BASE_TEMPLATE.format(
            name=self._name,
            python=self._settings["python"],
            http_port=int(self._settings["http_port"]),
            socketio_port=int(self._settings["socketio_port"]),
            default_branch=self._settings["default_branch"],
            app_repo=self._settings["app_repo"],
            mariadb_password=self._settings["mariadb_password"],
            redis_port=int(self._settings["redis_port"]),
            workers_default=int(self._settings["workers_default"]),
            workers_short=int(self._settings["workers_short"]),
            workers_long=int(self._settings["workers_long"]),
            admin_port=int(self._settings["admin_port"]),
            admin_enabled=_toml_bool(self._settings["admin_enabled"]),
            admin_password=self._settings["admin_password"],
        )

    def _render_volume(self) -> str:
        return _VOLUME_TEMPLATE.format(
            volume_pool=self._settings["volume_pool"],
            volume_device=self._settings["volume_device"],
            volume_benches_reservation=self._settings["volume_benches_reservation"],
            volume_benches_quota=self._settings["volume_benches_quota"],
            volume_mariadb_reservation=self._settings["volume_mariadb_reservation"],
            volume_mariadb_quota=self._settings["volume_mariadb_quota"],
            volume_mariadb_data_dir=self._settings["volume_mariadb_data_dir"],
            volume_snapshots_enabled=_toml_bool(self._settings["volume_snapshots_enabled"]),
        )

    @classmethod
    def read_settings(cls, toml_path: Path) -> dict:
        """Read bench.toml into the same flat-dict format as DEFAULTS.

        Missing keys fall back to DEFAULTS. bench_name is included (empty
        string if bench.name is absent so callers can substitute a path-based
        fallback).
        """
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)

        d = dict(cls.DEFAULTS)
        bench = data.get("bench", {})
        app = (data.get("apps") or [{}])[0]
        volume = data.get("volume", {})
        bvol = volume.get("benches", {})
        mvol = volume.get("mariadb", {})
        snap = volume.get("snapshots", {})

        d.update(
            {
                "bench_name": bench.get("name", ""),
                "python": bench.get("python", d["python"]),
                "http_port": bench.get("http_port", d["http_port"]),
                "socketio_port": bench.get("socketio_port", d["socketio_port"]),
                "mariadb_password": data.get("mariadb", {}).get("root_password", d["mariadb_password"]),
                "admin_password": data.get("admin", {}).get("password", d["admin_password"]),
                "app_repo": app.get("repo", d["app_repo"]),
                "default_branch": bench.get("default_branch") or app.get("branch", d["default_branch"]),
                "redis_port": data.get("redis", {}).get("port", d["redis_port"]),
                "workers_default": data.get("workers", {}).get("default", d["workers_default"]),
                "workers_short": data.get("workers", {}).get("short", d["workers_short"]),
                "workers_long": data.get("workers", {}).get("long", d["workers_long"]),
                "volume_enabled": volume.get("enabled", d["volume_enabled"]),
                "volume_pool": volume.get("pool", d["volume_pool"]),
                "volume_device": volume.get("device", d["volume_device"]),
                "volume_benches_reservation": bvol.get("reservation", d["volume_benches_reservation"]),
                "volume_benches_quota": bvol.get("quota", d["volume_benches_quota"]),
                "volume_mariadb_reservation": mvol.get("reservation", d["volume_mariadb_reservation"]),
                "volume_mariadb_quota": mvol.get("quota", d["volume_mariadb_quota"]),
                "volume_mariadb_data_dir": mvol.get("data_dir", d["volume_mariadb_data_dir"]),
                "volume_snapshots_enabled": snap.get("enabled", d["volume_snapshots_enabled"]),
            }
        )
        return d
