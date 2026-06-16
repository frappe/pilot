"""Tests for gunicorn production support."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bench_cli.config.app_config import AppConfig
from bench_cli.config.bench_config import BenchConfig
from bench_cli.config.gunicorn_config import GunicornConfig
from bench_cli.config.mariadb_config import MariaDBConfig
from bench_cli.config.redis_config import RedisConfig
from bench_cli.config.worker_config import WorkerConfig, WorkerGroup
from bench_cli.core.bench import Bench
from bench_cli.exceptions import ConfigError
from bench_cli.managers.gunicorn_manager import GunicornManager
from bench_cli.managers.process_manager import ProcessManager


def make_bench(tmp_path: Path, gunicorn: GunicornConfig | None = None) -> Bench:
    config = BenchConfig(
        name="test-bench",
        python_version="3.14",
        apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(groups=[
            WorkerGroup(queues=["default"], count=2),
            WorkerGroup(queues=["short"], count=1),
            WorkerGroup(queues=["long"], count=1),
        ]),
        gunicorn=gunicorn or GunicornConfig(),
    )
    return Bench(config, tmp_path)


# ── Config tests ──────────────────────────────────────────────────────────────


def test_gunicorn_config_defaults() -> None:
    cfg = GunicornConfig()
    assert cfg.workers == 4
    assert cfg.threads == 4
    assert cfg.timeout == 120
    assert cfg.worker_class == "sync"


def test_gunicorn_default_bind_uses_bench_http_port(tmp_path: Path) -> None:
    config = BenchConfig._from_dict({
        "bench": {"name": "test-bench", "python": "3.14", "http_port": 9000},
        "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
        "mariadb": {"root_password": "root"},
        "redis": {"cache_port": 13000, "queue_port": 11000},
    })
    bench = Bench(config, tmp_path)
    assert GunicornManager(bench)._bind() == "127.0.0.1:9000"


def test_bench_config_parses_gunicorn_section(tmp_path: Path) -> None:
    toml = tmp_path / "bench.toml"
    toml.write_text(
        '[bench]\nname = "test-bench"\npython = "3.14"\n\n'
        '[[apps]]\nname = "frappe"\nrepo = "https://github.com/frappe/frappe"\nbranch = "version-16"\n\n'
        '[mariadb]\nroot_password = "root"\n\n'
        '[redis]\ncache_port = 13000\nqueue_port = 11000\n\n'
        '[gunicorn]\nworkers = 8\nthreads = 16\ntimeout = 300\nworker_class = "gevent"\n'
    )
    config = BenchConfig.from_file(toml)
    assert config.gunicorn.workers == 8
    assert config.gunicorn.threads == 16
    assert config.gunicorn.timeout == 300
    assert config.gunicorn.worker_class == "gevent"


def test_gunicorn_workers_must_be_positive(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, GunicornConfig(workers=0))
    with pytest.raises(ConfigError, match="gunicorn.workers"):
        bench.config.validate()


def test_gunicorn_threads_must_be_positive(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, GunicornConfig(threads=0))
    with pytest.raises(ConfigError, match="gunicorn.threads"):
        bench.config.validate()


def test_gunicorn_timeout_must_be_positive(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, GunicornConfig(timeout=-1))
    with pytest.raises(ConfigError, match="gunicorn.timeout"):
        bench.config.validate()


def test_gunicorn_worker_class_must_not_be_empty(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, GunicornConfig(worker_class=""))
    with pytest.raises(ConfigError, match="gunicorn.worker_class"):
        bench.config.validate()


# ── GunicornManager tests ─────────────────────────────────────────────────────


def test_gunicorn_manager_generates_config_file(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config_path.mkdir(parents=True, exist_ok=True)

    GunicornManager(bench).generate_config()

    config_path = bench.config_path / "gunicorn.conf.py"
    assert config_path.exists()
    content = config_path.read_text()
    assert 'bind = "127.0.0.1:8000"' in content
    assert "workers = 4" in content
    assert "threads = 4" in content
    # threads > 0 forces gthread because sync workers ignore threads
    assert 'worker_class = "gthread"' in content
    assert "timeout = 120" in content
    assert "preload_app = True" in content


def test_gunicorn_manager_bind_uses_bench_http_port(tmp_path: Path) -> None:
    config = BenchConfig._from_dict({
        "bench": {"name": "test-bench", "python": "3.14", "http_port": 9000},
        "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
        "mariadb": {"root_password": "root"},
        "redis": {"cache_port": 13000, "queue_port": 11000},
    })
    bench = Bench(config, tmp_path)
    manager = GunicornManager(bench)

    assert manager._bind() == "127.0.0.1:9000"
    assert manager.upstream_server() == "127.0.0.1:9000"


# ── ProcessManager integration tests ──────────────────────────────────────────


def test_web_definition_uses_gunicorn_in_production(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    manager = ProcessManager(bench)

    pd = manager._web_definition(dev=False)

    assert "gunicorn" in pd.command
    assert "frappe.app:application" in pd.command
    assert "../config/gunicorn.conf.py" in pd.command
    assert "frappe serve" not in pd.command


def test_web_definition_uses_frappe_serve_in_dev(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    manager = ProcessManager(bench)

    pd = manager._web_definition(dev=True)

    assert "frappe serve" in pd.command
    assert "gunicorn" not in pd.command


def test_generate_config_writes_gunicorn_config(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    manager = ProcessManager(bench)

    with patch.object(manager, "_ensure_gunicorn_config", wraps=manager._ensure_gunicorn_config) as mock_ensure:
        manager.generate_config()
        mock_ensure.assert_called_once()

    assert (bench.config_path / "gunicorn.conf.py").exists()


def test_supervisor_generate_config_writes_gunicorn_config(tmp_path: Path) -> None:
    from bench_cli.managers.supervisor_process_manager import SupervisorProcessManager

    bench = make_bench(tmp_path)
    bench.config_path.mkdir(parents=True, exist_ok=True)
    manager = SupervisorProcessManager(bench)

    with patch("bench_cli.managers.admin_env_manager.AdminEnvManager"), \
         patch.object(manager, "_prod_process_definitions", return_value=[]):
        manager.generate_config()

    assert (bench.config_path / "gunicorn.conf.py").exists()


def test_systemd_generate_config_writes_gunicorn_config(tmp_path: Path) -> None:
    from bench_cli.managers.systemd_process_manager import SystemdProcessManager

    bench = make_bench(tmp_path)
    bench.config_path.mkdir(parents=True, exist_ok=True)
    manager = SystemdProcessManager(bench)

    with patch("bench_cli.managers.admin_env_manager.AdminEnvManager"), \
         patch.object(manager, "_prod_process_definitions", return_value=[]):
        manager.generate_config()

    assert (bench.config_path / "gunicorn.conf.py").exists()


# ── Nginx integration tests ───────────────────────────────────────────────────


def test_nginx_upstream_uses_gunicorn_bind(tmp_path: Path) -> None:
    from bench_cli.managers.nginx_manager import NginxManager

    config = BenchConfig._from_dict({
        "bench": {"name": "test-bench", "python": "3.14", "http_port": 9000},
        "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
        "mariadb": {"root_password": "root"},
        "redis": {"cache_port": 13000, "queue_port": 11000},
    })
    bench = Bench(config, tmp_path)
    manager = NginxManager(bench)

    upstream = manager._render_upstream_block("test-bench")

    assert "server 127.0.0.1:9000;" in upstream


# ── TOML writer tests ─────────────────────────────────────────────────────────


def test_toml_writer_includes_gunicorn_section(tmp_path: Path) -> None:
    from bench_cli.config.toml_writer import bench_config_to_toml

    bench = make_bench(tmp_path, GunicornConfig(workers=8, threads=16))
    toml = bench_config_to_toml(bench.config)

    assert "[gunicorn]" in toml
    assert "workers = 8" in toml
    assert "threads = 16" in toml
    assert "timeout = 120" in toml
    assert 'worker_class = "sync"' in toml
    assert "bind" not in toml
    assert "preload_app" not in toml


# ── Companion manager tests ───────────────────────────────────────────────────


def test_production_config_parses_use_companion_manager(tmp_path: Path) -> None:
    toml = tmp_path / "bench.toml"
    toml.write_text(
        '[bench]\nname = "test-bench"\npython = "3.14"\n\n'
        '[[apps]]\nname = "frappe"\nrepo = "https://github.com/frappe/frappe"\nbranch = "version-16"\n\n'
        '[mariadb]\nroot_password = "root"\n\n'
        '[redis]\ncache_port = 13000\nqueue_port = 11000\n\n'
        '[production]\nprocess_manager = "supervisor"\nnginx = true\nuse_companion_manager = true\n'
    )
    config = BenchConfig.from_file(toml)
    assert config.production.use_companion_manager is True


def test_toml_writer_includes_use_companion_manager(tmp_path: Path) -> None:
    from bench_cli.config.toml_writer import bench_config_to_toml

    bench = make_bench(tmp_path)
    bench.config.production.use_companion_manager = True
    toml = bench_config_to_toml(bench.config)

    assert "use_companion_manager = true" in toml


def test_gunicorn_config_includes_companion_workers(tmp_path: Path) -> None:
    config = BenchConfig._from_dict({
        "bench": {"name": "test-bench", "python": "3.14", "http_port": 8000, "socketio_port": 9000},
        "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
        "mariadb": {"root_password": "root"},
        "redis": {"cache_port": 13000, "queue_port": 11000},
        "production": {"process_manager": "supervisor", "use_companion_manager": True},
    })
    bench = Bench(config, tmp_path)
    bench.config_path.mkdir(parents=True, exist_ok=True)

    GunicornManager(bench).generate_config()

    content = (bench.config_path / "gunicorn.conf.py").read_text()
    assert "companion_control_socket" in content
    assert "companion_workers" in content
    assert "frappe.gunicorn_companion:run_scheduler" in content
    assert "frappe.gunicorn_companion:run_worker" in content
    assert "frappe.gunicorn_companion:run_socketio" in content
    assert 'wsgi_app = "frappe.app:application"' in content
    assert "on_starting" in content
    assert "when_ready" in content


def test_gunicorn_config_uses_explicit_combined_worker_group(tmp_path: Path) -> None:
    config = BenchConfig._from_dict({
        "bench": {"name": "test-bench", "python": "3.14", "http_port": 8000, "socketio_port": 9000},
        "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
        "mariadb": {"root_password": "root"},
        "redis": {"cache_port": 13000, "queue_port": 11000},
        "workers": [{"queues": ["default", "short", "long"], "count": 1}],
        "production": {"process_manager": "supervisor", "use_companion_manager": True},
    })
    bench = Bench(config, tmp_path)
    bench.config_path.mkdir(parents=True, exist_ok=True)

    GunicornManager(bench).generate_config()

    content = (bench.config_path / "gunicorn.conf.py").read_text()
    assert '"FRAPPE_COMPANION_QUEUE": "default,short,long"' in content
    assert '"FRAPPE_COMPANION_QUEUE": "short"' not in content
    assert '"FRAPPE_COMPANION_QUEUE": "long"' not in content


def test_gunicorn_config_excludes_companion_without_flag(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config_path.mkdir(parents=True, exist_ok=True)

    GunicornManager(bench).generate_config()

    content = (bench.config_path / "gunicorn.conf.py").read_text()
    assert "companion_workers" not in content
    assert "wsgi_app" not in content


def test_process_definitions_excludes_workers_and_socketio_in_companion_mode(tmp_path: Path) -> None:
    config = BenchConfig._from_dict({
        "bench": {"name": "test-bench", "python": "3.14"},
        "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
        "mariadb": {"root_password": "root"},
        "redis": {"cache_port": 13000, "queue_port": 11000},
        "production": {"process_manager": "supervisor", "use_companion_manager": True},
    })
    bench = Bench(config, tmp_path)
    manager = ProcessManager(bench)

    defs = manager._prod_process_definitions()
    names = {pd.name for pd in defs}

    assert "web" in names
    assert "admin" in names
    assert "redis_cache" in names
    assert "redis_queue" in names
    assert "socketio" not in names
    assert "worker_default_1" not in names
    assert "worker_short_1" not in names
    assert "worker_long_1" not in names


def test_supervisor_web_program_has_long_stopwaitsecs_in_companion_mode(tmp_path: Path) -> None:
    from bench_cli.managers.supervisor_process_manager import SupervisorProcessManager

    config = BenchConfig._from_dict({
        "bench": {"name": "test-bench", "python": "3.14"},
        "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
        "mariadb": {"root_password": "root"},
        "redis": {"cache_port": 13000, "queue_port": 11000},
        "production": {"process_manager": "supervisor", "use_companion_manager": True},
    })
    bench = Bench(config, tmp_path)
    bench.config_path.mkdir(parents=True, exist_ok=True)
    manager = SupervisorProcessManager(bench)

    web_pd = next(pd for pd in manager._prod_process_definitions() if pd.name == "web")
    program = manager._render_program(web_pd, "web")

    assert "stopwaitsecs=1600" in program


def test_systemd_web_service_has_long_timeout_in_companion_mode(tmp_path: Path) -> None:
    from bench_cli.managers.systemd_process_manager import SystemdProcessManager

    config = BenchConfig._from_dict({
        "bench": {"name": "test-bench", "python": "3.14"},
        "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
        "mariadb": {"root_password": "root"},
        "redis": {"cache_port": 13000, "queue_port": 11000},
        "production": {"process_manager": "systemd", "use_companion_manager": True},
    })
    bench = Bench(config, tmp_path)
    manager = SystemdProcessManager(bench)

    web_pd = next(pd for pd in manager._prod_process_definitions() if pd.name == "web")
    unit = manager._render_unit(web_pd)

    assert "TimeoutStopSec=1600" in unit
