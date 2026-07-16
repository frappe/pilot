from __future__ import annotations

import copy

from pilot.admin_url import admin_url
from pilot.config.bench_config import BenchConfig

BASE = {
    "bench": {"name": "alpha", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "r", "branch": "develop"}],
    "mariadb": {"root_password": "root"},
    "redis": {"cache_port": 13000, "queue_port": 11000},
}


def _cfg(**overrides):
    data = copy.deepcopy(BASE)
    data.update(overrides)
    return BenchConfig._from_dict(data)


def test_admin_url_dev_uses_localhost_and_port() -> None:
    cfg = _cfg(admin={"port": 7002})
    assert admin_url(cfg) == "http://localhost:7002"


def test_admin_url_dev_uses_supplied_host() -> None:
    cfg = _cfg(admin={"port": 7002})
    assert admin_url(cfg, dev_host="box.local") == "http://box.local:7002"


def test_admin_url_production_https() -> None:
    cfg = _cfg(
        admin={"domain": "admin-alpha.example.com", "tls": True},
        production={"enabled": True, "process_manager": "systemd"},
    )
    assert admin_url(cfg) == "https://admin-alpha.example.com"


def test_admin_url_production_http_when_tls_false() -> None:
    cfg = _cfg(
        admin={"domain": "admin-alpha.example.com", "tls": False},
        production={"enabled": True, "process_manager": "supervisor"},
    )
    assert admin_url(cfg) == "http://admin-alpha.example.com"
