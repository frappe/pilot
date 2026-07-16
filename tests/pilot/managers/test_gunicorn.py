"""Tests for gunicorn production support."""
from __future__ import annotations

import shlex
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config.app_config import AppConfig
from pilot.config.bench_config import BenchConfig
from pilot.config.gunicorn_config import GunicornConfig
from pilot.config.mariadb_config import MariaDBConfig
from pilot.config.redis_config import RedisConfig
from pilot.config.worker_config import WorkerConfig, WorkerGroup
from pilot.core.bench import Bench
from pilot.exceptions import ConfigError
from pilot.managers.gunicorn import GunicornManager
from pilot.managers.processes.local import ProcessManager


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
    assert cfg.workers == 2
    assert cfg.threads == 8
    assert cfg.timeout == 120
    assert cfg.worker_class == "gthread"
    assert cfg.malloc_arena_max == 2


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
    assert "workers = 2" in content
    assert "threads = 8" in content
    # threads > 0 forces gthread because sync workers ignore threads
    assert 'worker_class = "gthread"' in content
    assert "timeout = 120" in content
    assert "preload_app = True" in content


def test_gunicorn_manager_generates_admin_config(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config_path.mkdir(parents=True, exist_ok=True)

    GunicornManager(bench).generate_admin_config()

    config_path = bench.config_path / "admin-gunicorn.conf.py"
    assert config_path.exists()
    content = config_path.read_text()
    assert f'bind = "127.0.0.1:{bench.config.admin.internal_port}"' in content
    assert "workers = 1" in content
    assert "threads = 4" in content
    assert 'worker_class = "gthread"' in content
    # No preload so create_app (and its idle watchdog) runs in the worker.
    assert "preload_app = False" in content


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
    command_line = shlex.join(pd.argv)

    assert "gunicorn" in command_line
    assert "frappe.app:application" in command_line
    assert "../config/gunicorn.conf.py" in command_line
    assert "frappe serve" not in command_line


def test_admin_definition_uses_pinned_gunicorn_config(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)

    process = ProcessManager(bench)._admin_definition()

    assert process.argv[1:] == [
        "-c",
        str(bench.config_path / "admin-gunicorn.conf.py"),
        "admin.backend.wsgi:application",
    ]
    assert Path(process.argv[0]).name == "gunicorn"
    assert process.env["BENCH_ADMIN_ROOT"] == str(bench.path)
    assert process.working_dir is not None


def test_web_definition_uses_frappe_serve_in_dev(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    manager = ProcessManager(bench)

    pd = manager._web_definition(dev=True)
    command_line = shlex.join(pd.argv)

    assert "frappe serve" in command_line
    assert "gunicorn" not in command_line


def test_admin_runs_dev_server_on_admin_port_in_dev(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    manager = ProcessManager(bench, watch_admin_js=False)

    pd = manager._to_dev(manager._admin_definition())
    command_line = shlex.join(pd.argv)

    assert "admin.backend.run_server" in command_line
    assert "gunicorn" not in command_line
    assert f"--port {bench.config.admin.port}" in command_line
    assert "--no-timeout" in command_line
    assert "--dev" not in command_line


def test_admin_dev_server_enables_reload_when_watching(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    manager = ProcessManager(bench, watch_admin_js=True)

    pd = manager._to_dev(manager._admin_definition())
    command_line = shlex.join(pd.argv)

    assert "admin.backend.run_server" in command_line
    assert f"--port {bench.config.admin.port}" in command_line
    assert "--dev" in command_line


def test_generate_config_writes_gunicorn_config(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    manager = ProcessManager(bench)

    with patch.object(manager, "_ensure_gunicorn_config", wraps=manager._ensure_gunicorn_config) as mock_ensure:
        manager.write_config()
        mock_ensure.assert_called_once()

    assert (bench.config_path / "gunicorn.conf.py").exists()


def test_supervisor_generate_config_writes_gunicorn_config(tmp_path: Path) -> None:
    from pilot.managers.processes.supervisor import SupervisorProcessManager

    bench = make_bench(tmp_path)
    bench.config_path.mkdir(parents=True, exist_ok=True)
    manager = SupervisorProcessManager(bench)

    with patch("pilot.managers.admin_environment.AdminEnvManager"), \
         patch.object(manager, "_prod_process_definitions", return_value=[]):
        manager.write_config()

    assert (bench.config_path / "gunicorn.conf.py").exists()
    assert (bench.config_path / "admin-gunicorn.conf.py").exists()


def test_systemd_generate_config_writes_gunicorn_config(tmp_path: Path) -> None:
    from pilot.managers.processes.systemd import SystemdProcessManager

    bench = make_bench(tmp_path)
    bench.config_path.mkdir(parents=True, exist_ok=True)
    manager = SystemdProcessManager(bench)

    with patch("pilot.managers.admin_environment.AdminEnvManager"), \
         patch.object(manager, "_prod_process_definitions", return_value=[]):
        manager.write_config()

    assert (bench.config_path / "gunicorn.conf.py").exists()


# ── Nginx integration tests ───────────────────────────────────────────────────


def test_nginx_upstream_uses_gunicorn_bind(tmp_path: Path) -> None:
    from pilot.managers.nginx import NginxManager

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
    from pilot.config.bench_toml import dumps_config as bench_config_to_toml

    bench = make_bench(tmp_path, GunicornConfig(workers=8, threads=16))
    toml = bench_config_to_toml(bench.config)

    assert "[gunicorn]" in toml
    assert "workers = 8" in toml
    assert "threads = 16" in toml
    assert "timeout = 120" in toml
    assert 'worker_class = "gthread"' in toml
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
        '[admin]\ndomain = "admin.test.localhost"\n\n'
        '[production]\nprocess_manager = "supervisor"\nnginx = true\nuse_companion_manager = true\n'
    )
    config = BenchConfig.from_file(toml)
    assert config.production.use_companion_manager is True


def test_toml_writer_includes_use_companion_manager(tmp_path: Path) -> None:
    from pilot.config.bench_toml import dumps_config as bench_config_to_toml

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
    # A single worker-pool runs all queues with the scheduler embedded as a
    # thread, so there is no separate scheduler or per-group worker companion.
    assert "frappe.gunicorn_companion:run_worker_pool" in content
    assert "frappe.gunicorn_companion:run_socketio" in content
    assert "run_scheduler" not in content
    assert "FRAPPE_COMPANION_NUM_WORKERS" in content
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
    # A single group of one worker -> one pool worker.
    assert '"FRAPPE_COMPANION_NUM_WORKERS": "1"' in content


def test_worker_pool_aggregates_groups_into_one_pool(tmp_path: Path) -> None:
    # Multiple groups collapse into a single pool: deduped queue union and the
    # summed worker count drive one run_worker_pool companion.
    config = BenchConfig._from_dict({
        "bench": {"name": "test-bench", "python": "3.14", "http_port": 8000, "socketio_port": 9000},
        "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
        "mariadb": {"root_password": "root"},
        "redis": {"cache_port": 13000, "queue_port": 11000},
        "workers": [
            {"queues": ["default"], "count": 2},
            {"queues": ["short"], "count": 1},
            {"queues": ["long", "default"], "count": 1},
        ],
        "production": {"process_manager": "supervisor", "use_companion_manager": True},
    })
    bench = Bench(config, tmp_path)
    bench.config_path.mkdir(parents=True, exist_ok=True)

    GunicornManager(bench).generate_config()

    content = (bench.config_path / "gunicorn.conf.py").read_text()
    assert content.count("run_worker_pool") == 1
    assert '"FRAPPE_COMPANION_QUEUE": "default,short,long"' in content  # union, order-preserving, deduped
    assert '"FRAPPE_COMPANION_NUM_WORKERS": "4"' in content  # 2 + 1 + 1


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


def test_production_definitions_do_not_add_a_separate_task_worker(
    tmp_path: Path,
) -> None:
    names = {definition.name for definition in ProcessManager(make_bench(tmp_path))._prod_process_definitions()}

    assert "task_worker" not in names
    assert "task-worker" not in names


def test_supervisor_web_program_has_long_stopwaitsecs_in_companion_mode(tmp_path: Path) -> None:
    from pilot.managers.processes.supervisor import SupervisorProcessManager, SupervisorRenderer

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
    program = SupervisorRenderer("test-bench", bench.logs_path).render(web_pd)

    assert "stopwaitsecs=1600" in program


def test_systemd_web_service_has_long_timeout_in_companion_mode(tmp_path: Path) -> None:
    from pilot.managers.processes.systemd import SystemdProcessManager, SystemdRenderer

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
    unit = SystemdRenderer("test-bench").render(web_pd)

    assert "TimeoutStopSec=1600" in unit


def test_malloc_arena_max_in_units(tmp_path: Path) -> None:
    from pilot.managers.processes.supervisor import SupervisorRenderer
    from pilot.managers.processes.systemd import SystemdProcessManager, SystemdRenderer

    bench = make_bench(tmp_path, gunicorn=GunicornConfig())  # default arena 2
    systemd = SystemdProcessManager(bench)
    web = next(pd for pd in systemd._prod_process_definitions() if pd.name == "web")
    assert "Environment=MALLOC_ARENA_MAX=2" in SystemdRenderer("test-bench").render(web)
    assert 'MALLOC_ARENA_MAX="2"' in SupervisorRenderer("test-bench", bench.logs_path).render(web)

    # 0 disables the cap (no env emitted).
    bench0 = make_bench(tmp_path, gunicorn=GunicornConfig(malloc_arena_max=0))
    systemd0 = SystemdProcessManager(bench0)
    web0 = next(pd for pd in systemd0._prod_process_definitions() if pd.name == "web")
    assert "MALLOC_ARENA_MAX" not in SystemdRenderer("test-bench").render(web0)


def test_malloc_arena_max_validation(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        make_bench(tmp_path, gunicorn=GunicornConfig(malloc_arena_max=-1)).config.validate()


def test_py_memory_env_caps_glibc_arenas(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, GunicornConfig(malloc_arena_max=2))
    assert ProcessManager(bench)._py_memory_env() == {"MALLOC_ARENA_MAX": "2"}

    bench0 = make_bench(tmp_path, GunicornConfig(malloc_arena_max=0))
    assert ProcessManager(bench0)._py_memory_env() == {}


# ── max_requests worker recycling ───────────────────────────────────────────────


def test_max_requests_emitted_when_enabled(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, gunicorn=GunicornConfig(max_requests=2000, max_requests_jitter=200))
    bench.config_path.mkdir(parents=True, exist_ok=True)
    GunicornManager(bench).generate_config()
    content = (bench.config_path / "gunicorn.conf.py").read_text()
    assert "max_requests = 2000" in content
    assert "max_requests_jitter = 200" in content


def test_max_requests_absent_when_disabled(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, gunicorn=GunicornConfig(max_requests=0))
    bench.config_path.mkdir(parents=True, exist_ok=True)
    GunicornManager(bench).generate_config()
    content = (bench.config_path / "gunicorn.conf.py").read_text()
    assert "max_requests" not in content


def test_max_requests_validation(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="max_requests"):
        make_bench(tmp_path, gunicorn=GunicornConfig(max_requests=-1)).config.validate()
    with pytest.raises(ConfigError, match="max_requests_jitter"):
        make_bench(tmp_path, gunicorn=GunicornConfig(max_requests_jitter=-1)).config.validate()


def test_toml_writer_includes_max_requests(tmp_path: Path) -> None:
    from pilot.config.bench_toml import dumps_config as bench_config_to_toml

    bench = make_bench(tmp_path, GunicornConfig(max_requests=2000, max_requests_jitter=200))
    toml = bench_config_to_toml(bench.config)
    assert "max_requests = 2000" in toml
    assert "max_requests_jitter = 200" in toml
