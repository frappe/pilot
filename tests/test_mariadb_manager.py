"""Tests for MariaDBManager.create_user() and _grant_host()."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from bench_cli.config.bench_config import BenchConfig
from bench_cli.config.mariadb_config import MariaDBConfig
from bench_cli.config.site_config import SiteConfig
from bench_cli.core.bench import Bench
from bench_cli.core.site import Site
from bench_cli.managers.mariadb_manager import MariaDBManager


def make_manager(host: str = "localhost", port: int = 3306, root_password: str = "secret") -> MariaDBManager:
    return MariaDBManager(MariaDBConfig(host=host, port=port, root_password=root_password))


# ── _grant_host() ─────────────────────────────────────────────────────────────


def test_grant_host_returns_percent_when_no_socket() -> None:
    manager = make_manager()
    with patch.object(manager, "_detect_socket", return_value=""):
        assert manager._grant_host() == "%"


def test_grant_host_returns_localhost_when_socket_detected() -> None:
    manager = make_manager()
    with patch.object(manager, "_detect_socket", return_value="/tmp/mysql.sock"):
        assert manager._grant_host() == "localhost"


# ── create_user() ─────────────────────────────────────────────────────────────


def test_create_user_tcp_uses_percent_host() -> None:
    manager = make_manager(host="127.0.0.1", root_password="root")
    with patch.object(manager, "_detect_socket", return_value=""):
        with patch("shutil.which", side_effect=lambda b: "/usr/bin/mysql" if b == "mysql" else None):
            with patch("bench_cli.managers.mariadb_manager.run_command") as mock_run:
                manager.create_user("mydb", "mypass", "mydb")
                called_cmd = mock_run.call_args[0][0]
                assert any("'mydb'@'%'" in part for part in called_cmd)


def test_create_user_socket_uses_localhost_host() -> None:
    manager = make_manager(root_password="root")
    with patch.object(manager, "_detect_socket", return_value="/tmp/mysql.sock"):
        with patch("shutil.which", side_effect=lambda b: "/usr/bin/mysql" if b == "mysql" else None):
            with patch("bench_cli.managers.mariadb_manager.run_command") as mock_run:
                manager.create_user("mydb", "mypass", "mydb")
                called_cmd = mock_run.call_args[0][0]
                assert any("'mydb'@'localhost'" in part for part in called_cmd)


def test_create_user_prefers_mariadb_binary() -> None:
    manager = make_manager(root_password="root")
    with patch.object(manager, "_detect_socket", return_value=""):
        with patch("shutil.which", side_effect=lambda b: "/usr/bin/mariadb" if b == "mariadb" else None):
            with patch("bench_cli.managers.mariadb_manager.run_command") as mock_run:
                manager.create_user("mydb", "mypass", "mydb")
                called_cmd = mock_run.call_args[0][0]
                assert called_cmd[0] == "/usr/bin/mariadb"


def test_create_user_falls_back_to_mysql_binary() -> None:
    manager = make_manager(root_password="root")
    with patch.object(manager, "_detect_socket", return_value=""):
        with patch("shutil.which", side_effect=lambda b: "/usr/bin/mysql" if b == "mysql" else None):
            with patch("bench_cli.managers.mariadb_manager.run_command") as mock_run:
                manager.create_user("mydb", "mypass", "mydb")
                called_cmd = mock_run.call_args[0][0]
                assert called_cmd[0] == "/usr/bin/mysql"


def test_create_user_tcp_passes_host_and_port() -> None:
    manager = make_manager(host="127.0.0.1", port=3307, root_password="root")
    with patch.object(manager, "_detect_socket", return_value=""):
        with patch("shutil.which", return_value="/usr/bin/mysql"):
            with patch("bench_cli.managers.mariadb_manager.run_command") as mock_run:
                manager.create_user("mydb", "mypass", "mydb")
                called_cmd = mock_run.call_args[0][0]
                assert "-h127.0.0.1" in called_cmd
                assert "-P3307" in called_cmd


def test_create_user_socket_passes_socket_path() -> None:
    manager = make_manager(root_password="root")
    with patch.object(manager, "_detect_socket", return_value="/tmp/mysql.sock"):
        with patch("shutil.which", return_value="/usr/bin/mysql"):
            with patch("bench_cli.managers.mariadb_manager.run_command") as mock_run:
                manager.create_user("mydb", "mypass", "mydb")
                called_cmd = mock_run.call_args[0][0]
                assert "--socket=/tmp/mysql.sock" in called_cmd


def test_create_user_includes_root_password() -> None:
    manager = make_manager(root_password="s3cr3t")
    with patch.object(manager, "_detect_socket", return_value=""):
        with patch("shutil.which", return_value="/usr/bin/mysql"):
            with patch("bench_cli.managers.mariadb_manager.run_command") as mock_run:
                manager.create_user("mydb", "mypass", "mydb")
                called_cmd = mock_run.call_args[0][0]
                assert "-ps3cr3t" in called_cmd


# ── site.create() post-processing ────────────────────────────────────────────


def make_bench(tmp_path: Path) -> Bench:
    from bench_cli.config.app_config import AppConfig
    from bench_cli.config.redis_config import RedisConfig
    from bench_cli.config.worker_config import WorkerConfig
    config = BenchConfig(
        name="test-bench",
        python_version="3.14",
        apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
        mariadb=MariaDBConfig(host="127.0.0.1", port=3306, root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000, socketio_port=12000),
        workers=__import__("bench_cli.config.worker_config", fromlist=["WorkerConfig"]).WorkerConfig(default_count=1, short_count=1, long_count=1),
    )
    return Bench(config, tmp_path)


def test_site_create_calls_create_user_after_frappe_new_site(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    site = Site(SiteConfig(name="site1.localhost", apps=["frappe"]), bench)

    site_cfg_data = {"db_name": "_abc123", "db_password": "dbpass"}
    site.path.mkdir(parents=True, exist_ok=True)
    (site.path / "site_config.json").write_text(json.dumps(site_cfg_data))

    with patch("bench_cli.core.site.run_command"):
        with patch("bench_cli.managers.mariadb_manager.MariaDBManager._detect_socket", return_value=""):
            with patch("bench_cli.managers.mariadb_manager.MariaDBManager.create_user") as mock_create:
                site.create()
                mock_create.assert_called_once_with("_abc123", "dbpass", "_abc123")


def test_site_create_skips_create_user_when_socket_used(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    site = Site(SiteConfig(name="site1.localhost", apps=["frappe"]), bench)

    site.path.mkdir(parents=True, exist_ok=True)
    (site.path / "site_config.json").write_text(json.dumps({"db_name": "_abc123", "db_password": "dbpass"}))

    with patch("bench_cli.core.site.run_command"):
        with patch("bench_cli.managers.mariadb_manager.MariaDBManager._detect_socket", return_value="/tmp/mysql.sock"):
            with patch("bench_cli.managers.mariadb_manager.MariaDBManager.create_user") as mock_create:
                site.create()
                mock_create.assert_not_called()


def test_site_create_skips_create_user_when_site_config_missing(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    site = Site(SiteConfig(name="site1.localhost", apps=["frappe"]), bench)

    with patch("bench_cli.core.site.run_command"):
        with patch("bench_cli.managers.mariadb_manager.MariaDBManager._detect_socket", return_value=""):
            with patch("bench_cli.managers.mariadb_manager.MariaDBManager.create_user") as mock_create:
                site.create()
                mock_create.assert_not_called()
