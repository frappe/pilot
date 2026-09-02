"""Tests for gunicorn production support."""

from __future__ import annotations

import shlex
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import (
    AppConfig,
    BenchConfig,
    GunicornConfig,
    MariaDBConfig,
    RedisConfig,
    WorkerConfig,
    WorkerGroup,
)
from pilot.core.bench import Bench
from pilot.exceptions import ConfigError
from pilot.managers.gunicorn import GunicornManager
from pilot.managers.processes.definitions import ProcessDefinitionBuilder
from pilot.managers.processes.local import ProcessManager


def _definitions(bench: Bench, watch_admin_js: bool = False) -> ProcessDefinitionBuilder:
    return ProcessDefinitionBuilder(bench, bench.env_path / "bin" / "python", watch_admin_js)


def make_bench(tmp_path: Path, gunicorn: GunicornConfig | None = None) -> Bench:
    config = BenchConfig(
        name="test-bench",
        python_version="3.14",
        apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(
            groups=[
                WorkerGroup(queues=["default"], count=2),
                WorkerGroup(queues=["short"], count=1),
                WorkerGroup(queues=["long"], count=1),
            ]
        ),
        gunicorn=gunicorn or GunicornConfig(),
    )
    return Bench(config, tmp_path)


def test_gunicorn_config_defaults() -> None:
    cfg = GunicornConfig()
    assert cfg.workers == 2
    assert cfg.threads == 8
    assert cfg.timeout == 120
    assert cfg.worker_class == "gthread"


def test_gunicorn_default_bind_uses_bench_http_port(tmp_path: Path) -> None:
    config = BenchConfig._from_dict(
        {
            "bench": {"name": "test-bench", "python": "3.14", "http_port": 9000},
            "apps": [
                {
                    "name": "frappe",
                    "repo": "https://github.com/frappe/frappe",
                    "branch": "version-16",
                }
            ],
            "mariadb": {"root_password": "root"},
            "redis": {"cache_port": 13000, "queue_port": 11000},
        }
    )
    bench = Bench(config, tmp_path)
    assert GunicornManager(bench)._bind() == "127.0.0.1:9000"


def test_bench_config_parses_gunicorn_section(tmp_path: Path) -> None:
    toml = tmp_path / "bench.toml"
    toml.write_text(
        '[bench]\nname = "test-bench"\npython = "3.14"\n\n'
        '[[apps]]\nname = "frappe"\nrepo = "https://github.com/frappe/frappe"\nbranch = "version-16"\n\n'
        '[mariadb]\nroot_password = "root"\n\n'
        "[redis]\ncache_port = 13000\nqueue_port = 11000\n\n"
        '[gunicorn]\nworkers = 8\nthreads = 16\ntimeout = 300\nworker_class = "gevent"\n'
    )
    config = BenchConfig.from_file(toml)
    assert config.gunicorn.workers == 8
    assert config.gunicorn.threads == 16
    assert config.gunicorn.timeout == 300
    assert config.gunicorn.worker_class == "gevent"


def test_gunicorn_workers_must_be_positive(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, GunicornConfig(workers=0))
    with pytest.raises(ConfigError, match=r"gunicorn\.workers"):
        bench.config.validate()


def test_gunicorn_threads_must_be_positive(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, GunicornConfig(threads=0))
    with pytest.raises(ConfigError, match=r"gunicorn\.threads"):
        bench.config.validate()


def test_gunicorn_timeout_must_be_positive(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, GunicornConfig(timeout=-1))
    with pytest.raises(ConfigError, match=r"gunicorn\.timeout"):
        bench.config.validate()


def test_gunicorn_worker_class_must_not_be_empty(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, GunicornConfig(worker_class=""))
    with pytest.raises(ConfigError, match=r"gunicorn\.worker_class"):
        bench.config.validate()


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
    config = BenchConfig._from_dict(
        {
            "bench": {"name": "test-bench", "python": "3.14", "http_port": 9000},
            "apps": [
                {
                    "name": "frappe",
                    "repo": "https://github.com/frappe/frappe",
                    "branch": "version-16",
                }
            ],
            "mariadb": {"root_password": "root"},
            "redis": {"cache_port": 13000, "queue_port": 11000},
        }
    )
    bench = Bench(config, tmp_path)
    manager = GunicornManager(bench)

    assert manager._bind() == "127.0.0.1:9000"
    assert manager.upstream_server == "127.0.0.1:9000"


def test_web_definition_uses_gunicorn_in_production(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)

    pd = _definitions(bench).web_definition(dev=False)
    command_line = shlex.join(pd.argv)

    assert "gunicorn" in command_line
    assert "frappe.app:application" in command_line
    assert "../config/gunicorn.conf.py" in command_line
    assert "frappe serve" not in command_line


def test_admin_definition_uses_pinned_gunicorn_config(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)

    process = _definitions(bench).admin_definition()

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

    pd = _definitions(bench).web_definition(dev=True)
    command_line = shlex.join(pd.argv)

    assert "frappe serve" in command_line
    assert "gunicorn" not in command_line


def test_admin_runs_dev_server_on_admin_port_in_dev(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    definitions = _definitions(bench, watch_admin_js=False)

    pd = definitions.to_dev(definitions.admin_definition())
    command_line = shlex.join(pd.argv)

    assert "admin.backend.run_server" in command_line
    assert "gunicorn" not in command_line
    assert f"--port {bench.config.admin.port}" in command_line
    assert "--no-timeout" in command_line
    assert "--dev" not in command_line


def test_admin_dev_server_enables_reload_when_watching(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    definitions = _definitions(bench, watch_admin_js=True)

    pd = definitions.to_dev(definitions.admin_definition())
    command_line = shlex.join(pd.argv)

    assert "admin.backend.run_server" in command_line
    assert f"--port {bench.config.admin.port}" in command_line
    assert "--dev" in command_line


def test_generate_config_writes_gunicorn_config(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    manager = ProcessManager(bench)

    with patch.object(
        manager, "_ensure_gunicorn_config", wraps=manager._ensure_gunicorn_config
    ) as mock_ensure:
        manager.write_config()
        mock_ensure.assert_called_once()

    assert (bench.config_path / "gunicorn.conf.py").exists()


def test_supervisor_generate_config_writes_gunicorn_config(tmp_path: Path) -> None:
    from pilot.managers.processes.supervisor import SupervisorProcessManager

    bench = make_bench(tmp_path)
    bench.config_path.mkdir(parents=True, exist_ok=True)
    manager = SupervisorProcessManager(bench)

    with (
        patch("pilot.managers.environment.AdminEnvManager"),
        patch.object(manager, "_prod_process_definitions", return_value=[]),
    ):
        manager.write_config()

    assert (bench.config_path / "gunicorn.conf.py").exists()
    assert (bench.config_path / "admin-gunicorn.conf.py").exists()


def test_systemd_generate_config_writes_gunicorn_config(tmp_path: Path) -> None:
    from pilot.managers.processes.systemd import SystemdProcessManager

    bench = make_bench(tmp_path)
    bench.config_path.mkdir(parents=True, exist_ok=True)
    manager = SystemdProcessManager(bench)

    with (
        patch("pilot.managers.environment.AdminEnvManager"),
        patch.object(manager, "_prod_process_definitions", return_value=[]),
    ):
        manager.write_config()

    assert (bench.config_path / "gunicorn.conf.py").exists()


def test_nginx_upstream_uses_gunicorn_bind(tmp_path: Path) -> None:
    from pilot.managers.nginx import NginxConfigRenderer

    config = BenchConfig._from_dict(
        {
            "bench": {"name": "test-bench", "python": "3.14", "http_port": 9000},
            "apps": [
                {
                    "name": "frappe",
                    "repo": "https://github.com/frappe/frappe",
                    "branch": "version-16",
                }
            ],
            "mariadb": {"root_password": "root"},
            "redis": {"cache_port": 13000, "queue_port": 11000},
        }
    )
    bench = Bench(config, tmp_path)
    renderer = NginxConfigRenderer(bench)
    renderer._proxy_servers_cache = []

    config_text = renderer.generate_bench_config([], admin_ssl=False)

    assert "upstream bench-test-bench {" in config_text
    assert "server 127.0.0.1:9000;" in config_text


def test_toml_writer_includes_gunicorn_section(tmp_path: Path) -> None:

    bench = make_bench(tmp_path, GunicornConfig(workers=8, threads=16))
    toml = bench.config.dumps()

    assert "[gunicorn]" in toml
    assert "workers = 8" in toml
    assert "threads = 16" in toml
    assert "timeout = 120" in toml
    assert 'worker_class = "gthread"' in toml
    assert "bind" not in toml
    assert "preload_app" not in toml


def test_production_definitions_do_not_add_a_separate_task_worker(
    tmp_path: Path,
) -> None:
    names = {
        definition.name for definition in ProcessManager(make_bench(tmp_path))._prod_process_definitions()
    }

    assert "task_worker" not in names
    assert "task-worker" not in names


def test_only_lite_and_admin_cap_glibc_arenas(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)

    assert "MALLOC_ARENA_MAX" not in _definitions(bench).python_env()
    admin = next(pd for pd in _definitions(bench).prod_process_definitions() if pd.name == "admin")
    assert admin.env["MALLOC_ARENA_MAX"] == "2"


def test_python_processes_run_unbuffered(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, GunicornConfig())
    definitions = _definitions(bench)

    dev = {pd.name: pd for pd in definitions.process_definitions()}
    prod = {pd.name: pd for pd in definitions.prod_process_definitions()}

    for name in ("web", "watch", "admin"):
        assert dev[name].env.get("PYTHONUNBUFFERED") == "1", name
    for name in ("web", "admin"):
        assert prod[name].env.get("PYTHONUNBUFFERED") == "1", name
    assert all(
        pd.env.get("PYTHONUNBUFFERED") == "1" for name, pd in prod.items() if name.startswith("worker")
    )

    assert "PYTHONUNBUFFERED" not in prod["redis_cache"].env
    assert dev["web"].env["DEV_SERVER"] == "1"


def test_unbuffered_env_reaches_service_units(tmp_path: Path) -> None:
    from pilot.managers.processes.supervisor import SupervisorRenderer
    from pilot.managers.processes.systemd import SystemdRenderer

    bench = make_bench(tmp_path, GunicornConfig())
    web = next(pd for pd in _definitions(bench).prod_process_definitions() if pd.name == "web")

    # Quoted: an unquoted value with a space would split into a second assignment.
    assert 'Environment="PYTHONUNBUFFERED=1"' in SystemdRenderer("test-bench").render(web)
    assert 'PYTHONUNBUFFERED="1"' in SupervisorRenderer("test-bench", bench.logs_path).render(web)


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

    bench = make_bench(tmp_path, GunicornConfig(max_requests=2000, max_requests_jitter=200))
    toml = bench.config.dumps()
    assert "max_requests = 2000" in toml
    assert "max_requests_jitter = 200" in toml
