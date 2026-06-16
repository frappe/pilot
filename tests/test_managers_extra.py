"""Unit tests for RedisManager and SupervisorProcessManager."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from bench_cli.config.app_config import AppConfig
from bench_cli.config.bench_config import BenchConfig
from bench_cli.config.mariadb_config import MariaDBConfig
from bench_cli.config.redis_config import RedisConfig
from bench_cli.config.worker_config import WorkerConfig, WorkerGroup
from bench_cli.core.bench import Bench
from bench_cli.managers.redis_manager import RedisManager


def make_bench(tmp_path: Path) -> Bench:
    config = BenchConfig(
        name="test-bench",
        python_version="3.14",
        apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(groups=[
            WorkerGroup(queues=["default"], count=1),
            WorkerGroup(queues=["short"], count=1),
            WorkerGroup(queues=["long"], count=1),
        ]),
    )
    bench = Bench(config, tmp_path)
    bench.config_path.mkdir(parents=True, exist_ok=True)
    bench.logs_path.mkdir(parents=True, exist_ok=True)
    return bench


# ── RedisManager ──────────────────────────────────────────────────────────────


def test_redis_manager_writes_two_configs(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    redis_cfg = RedisConfig(cache_port=13000, queue_port=11000)

    manager = RedisManager(redis_cfg, bench)
    manager.generate_configs()

    assert (bench.config_path / "redis_cache.conf").exists()
    assert (bench.config_path / "redis_queue.conf").exists()
    assert not (bench.config_path / "redis_socketio.conf").exists()
    assert not (bench.config_path / "redis.conf").exists()


def test_redis_manager_multi_config_ports(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    redis_cfg = RedisConfig(cache_port=13000, queue_port=11000)
    RedisManager(redis_cfg, bench).generate_configs()

    assert "port 13000" in (bench.config_path / "redis_cache.conf").read_text()
    assert "port 11000" in (bench.config_path / "redis_queue.conf").read_text()


def test_redis_manager_cache_config_has_no_save(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    redis_cfg = RedisConfig(cache_port=13000, queue_port=11000)
    RedisManager(redis_cfg, bench).generate_configs()

    cache = (bench.config_path / "redis_cache.conf").read_text()
    assert 'save ""' in cache


def test_redis_manager_brew_package_with_version() -> None:
    bench = MagicMock()
    redis_cfg = RedisConfig(version="7.2")
    manager = RedisManager(redis_cfg, bench)
    assert manager._brew_package() == "redis@7.2"


def test_redis_manager_brew_package_no_version() -> None:
    bench = MagicMock()
    redis_cfg = RedisConfig()
    manager = RedisManager(redis_cfg, bench)
    assert manager._brew_package() == "redis"


def test_redis_manager_is_installed_true() -> None:
    bench = MagicMock()
    manager = RedisManager(RedisConfig(), bench)
    with patch("shutil.which", return_value="/usr/bin/redis-server"):
        assert manager.is_installed() is True


def test_redis_manager_is_installed_false() -> None:
    bench = MagicMock()
    manager = RedisManager(RedisConfig(), bench)
    with patch("shutil.which", return_value=None):
        assert manager.is_installed() is False


# ── SupervisorProcessManager ──────────────────────────────────────────────────


def _make_supervisor_manager(tmp_path: Path):
    from bench_cli.managers.supervisor_process_manager import SupervisorProcessManager
    bench = make_bench(tmp_path)
    (tmp_path / "config" / "supervisor").mkdir(parents=True, exist_ok=True)
    return SupervisorProcessManager(bench)


def test_supervisor_render_program_extracts_cd_prefix(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition

    mgr = _make_supervisor_manager(tmp_path)
    pd = ProcessDefinition(
        name="web",
        command="cd /sites && /env/bin/python -m frappe.utils.bench_helper frappe serve",
        log_file=tmp_path / "logs" / "web.log",
    )
    block = mgr._render_program(pd, "web")
    assert "directory=/sites" in block
    assert "command=/env/bin/python" in block
    assert "cd /sites" not in block


def test_supervisor_render_program_extracts_env_vars(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition

    mgr = _make_supervisor_manager(tmp_path)
    pd = ProcessDefinition(
        name="admin",
        command="PYTHONPATH=/cli FOO=bar /env/bin/python -m admin.backend.server",
        log_file=tmp_path / "logs" / "admin.log",
    )
    block = mgr._render_program(pd, "admin")
    assert 'environment=' in block
    assert 'PYTHONPATH="/cli"' in block
    assert 'FOO="bar"' in block
    assert "command=/env/bin/python" in block


def test_supervisor_render_program_no_prefix(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition

    mgr = _make_supervisor_manager(tmp_path)
    pd = ProcessDefinition(
        name="redis_cache",
        command="redis-server /config/redis_cache.conf",
        log_file=tmp_path / "logs" / "redis_cache.log",
    )
    block = mgr._render_program(pd, "redis-cache")
    assert "command=redis-server" in block
    assert "directory=" not in block
    assert "environment=" not in block


def test_supervisor_render_conf_has_group_section(tmp_path: Path) -> None:
    mgr = _make_supervisor_manager(tmp_path)
    with patch.object(mgr, "_prod_process_definitions", return_value=[]):
        conf = mgr._render_supervisord_conf()
    assert "[group:test-bench]" in conf


def test_supervisor_render_conf_has_unix_http_server(tmp_path: Path) -> None:
    mgr = _make_supervisor_manager(tmp_path)
    with patch.object(mgr, "_prod_process_definitions", return_value=[]):
        conf = mgr._render_supervisord_conf()
    assert "[unix_http_server]" in conf
    assert f"file={mgr.supervisor_sock}" in conf


def test_supervisor_render_conf_program_names_in_group(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition

    mgr = _make_supervisor_manager(tmp_path)
    fake_defs = [
        ProcessDefinition("web", "cmd_web", tmp_path / "logs" / "web.log"),
        ProcessDefinition("worker_default_1", "cmd_worker", tmp_path / "logs" / "w.log"),
    ]
    with patch.object(mgr, "_prod_process_definitions", return_value=fake_defs):
        conf = mgr._render_supervisord_conf()
    assert "test-bench-web" in conf
    assert "test-bench-worker-default-1" in conf


def test_supervisor_conf_path(tmp_path: Path) -> None:
    mgr = _make_supervisor_manager(tmp_path)
    assert mgr.supervisor_conf_path == tmp_path / "config" / "supervisor" / "supervisord.conf"


def test_supervisor_sock_path(tmp_path: Path) -> None:
    mgr = _make_supervisor_manager(tmp_path)
    assert mgr.supervisor_sock == tmp_path / "config" / "supervisor" / "supervisord.sock"


def test_supervisor_pid_path(tmp_path: Path) -> None:
    mgr = _make_supervisor_manager(tmp_path)
    assert mgr.supervisor_pid == tmp_path / "config" / "supervisor" / "supervisord.pid"


def test_supervisor_generate_config_writes_file(tmp_path: Path) -> None:
    mgr = _make_supervisor_manager(tmp_path)
    with patch("bench_cli.managers.supervisor_process_manager.AdminEnvManager"):
        with patch.object(mgr, "_render_supervisord_conf", return_value="[group:test-bench]\nprograms=\n\n"):
            mgr.generate_config()
    assert mgr.supervisor_conf_path.exists()


def test_supervisor_render_conf_no_user_directive(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition

    mgr = _make_supervisor_manager(tmp_path)
    fake_defs = [ProcessDefinition("web", "cmd_web", tmp_path / "logs" / "web.log")]
    with patch.object(mgr, "_prod_process_definitions", return_value=fake_defs):
        conf = mgr._render_supervisord_conf()
    assert "user=" not in conf


def test_supervisor_is_configured_false_when_no_conf(tmp_path: Path) -> None:
    mgr = _make_supervisor_manager(tmp_path)
    assert mgr.is_configured() is False


def test_supervisor_is_configured_true_when_conf_exists(tmp_path: Path) -> None:
    mgr = _make_supervisor_manager(tmp_path)
    mgr.supervisor_conf_path.write_text("[supervisord]\n")
    assert mgr.is_configured() is True


def test_supervisor_supervisorctl_uses_local_conf(tmp_path: Path) -> None:
    mgr = _make_supervisor_manager(tmp_path)
    cmd = mgr._supervisorctl()
    assert cmd == ["supervisorctl", "-c", str(mgr.supervisor_conf_path)]


# ── SystemdProcessManager ─────────────────────────────────────────────────────


def _make_systemd_manager(tmp_path: Path):
    from bench_cli.managers.systemd_process_manager import SystemdProcessManager
    bench = make_bench(tmp_path)
    return SystemdProcessManager(bench)


def test_systemd_unit_name(tmp_path: Path) -> None:
    mgr = _make_systemd_manager(tmp_path)
    assert mgr._unit_name("web") == "test-bench-web.service"


def test_systemd_target_name(tmp_path: Path) -> None:
    mgr = _make_systemd_manager(tmp_path)
    assert mgr._target_name() == "test-bench.target"


def test_systemd_user_unit_dir(tmp_path: Path) -> None:
    mgr = _make_systemd_manager(tmp_path)
    assert mgr.user_unit_dir == Path.home() / ".config" / "systemd" / "user"


def test_systemd_systemctl_cmd_includes_user_flag(tmp_path: Path) -> None:
    mgr = _make_systemd_manager(tmp_path)
    assert mgr._systemctl("start", "foo.target") == ["systemctl", "--user", "start", "foo.target"]


def test_systemd_env_sets_xdg_runtime_dir(tmp_path: Path) -> None:
    import os
    mgr = _make_systemd_manager(tmp_path)
    env = mgr._systemctl_env()
    assert env["XDG_RUNTIME_DIR"] == f"/run/user/{os.getuid()}"


def test_systemd_render_unit_extracts_cd_prefix(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition

    mgr = _make_systemd_manager(tmp_path)
    pd = ProcessDefinition(
        name="web",
        command="cd /sites && /env/bin/python -m frappe.utils.bench_helper frappe serve",
        log_file=tmp_path / "logs" / "web.log",
    )
    unit = mgr._render_unit(pd)
    assert "WorkingDirectory=/sites" in unit
    assert "ExecStart=/env/bin/python" in unit
    assert "cd /sites" not in unit


def test_systemd_render_unit_extracts_env_vars(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition

    mgr = _make_systemd_manager(tmp_path)
    pd = ProcessDefinition(
        name="admin",
        command="PYTHONPATH=/cli FOO=bar /env/bin/python -m admin.backend.server",
        log_file=tmp_path / "logs" / "admin.log",
    )
    unit = mgr._render_unit(pd)
    assert "Environment=PYTHONPATH=/cli" in unit
    assert "Environment=FOO=bar" in unit
    assert "ExecStart=/env/bin/python" in unit


def test_systemd_render_unit_no_user_directive(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition

    mgr = _make_systemd_manager(tmp_path)
    pd = ProcessDefinition(
        name="web",
        command="/env/bin/python serve",
        log_file=tmp_path / "logs" / "web.log",
    )
    unit = mgr._render_unit(pd)
    assert "User=" not in unit


def test_systemd_render_unit_part_of_target(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition

    mgr = _make_systemd_manager(tmp_path)
    pd = ProcessDefinition(name="web", command="/env/bin/python serve", log_file=tmp_path / "logs" / "web.log")
    unit = mgr._render_unit(pd)
    assert f"PartOf={mgr._target_name()}" in unit


def test_systemd_render_target_wanted_by_default(tmp_path: Path) -> None:
    mgr = _make_systemd_manager(tmp_path)
    target = mgr._render_target([])
    assert "WantedBy=default.target" in target


def test_systemd_generate_config_writes_unit_files(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition

    mgr = _make_systemd_manager(tmp_path)
    mgr.systemd_conf_dir.mkdir(parents=True, exist_ok=True)
    fake_defs = [ProcessDefinition("web", "/env/bin/python serve", tmp_path / "logs" / "web.log")]
    with patch("bench_cli.managers.admin_env_manager.AdminEnvManager"):
        with patch.object(mgr, "_prod_process_definitions", return_value=fake_defs):
            mgr.generate_config()
    assert (mgr.systemd_conf_dir / "test-bench-web.service").exists()
    assert (mgr.systemd_conf_dir / "test-bench.target").exists()


def test_systemd_is_running_true_when_systemctl_exits_zero(tmp_path: Path) -> None:
    mgr = _make_systemd_manager(tmp_path)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert mgr.is_running() is True


def test_systemd_is_running_false_when_systemctl_exits_nonzero(tmp_path: Path) -> None:
    mgr = _make_systemd_manager(tmp_path)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert mgr.is_running() is False


def test_systemd_is_running_false_when_systemctl_not_installed(tmp_path: Path) -> None:
    mgr = _make_systemd_manager(tmp_path)
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert mgr.is_running() is False


def test_systemd_is_configured_true_when_target_enabled(tmp_path: Path) -> None:
    mgr = _make_systemd_manager(tmp_path)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert mgr.is_configured() is True


def test_systemd_is_configured_false_when_target_not_enabled(tmp_path: Path) -> None:
    mgr = _make_systemd_manager(tmp_path)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert mgr.is_configured() is False


# ── SupervisorProcessManager — runtime ────────────────────────────────────────


def test_supervisor_is_alive_false_when_no_pid_file(tmp_path: Path) -> None:
    mgr = _make_supervisor_manager(tmp_path)
    assert mgr.is_alive() is False


def test_supervisor_is_alive_true_when_process_running(tmp_path: Path) -> None:
    import os
    mgr = _make_supervisor_manager(tmp_path)
    mgr.supervisor_pid.write_text(str(os.getpid()))
    assert mgr.is_alive() is True


def test_supervisor_is_alive_false_when_process_dead(tmp_path: Path) -> None:
    mgr = _make_supervisor_manager(tmp_path)
    mgr.supervisor_pid.write_text("999999")  # non-existent PID
    assert mgr.is_alive() is False


def test_supervisor_is_running_false_when_not_configured(tmp_path: Path) -> None:
    mgr = _make_supervisor_manager(tmp_path)
    # conf file absent — is_configured() short-circuits before any subprocess call
    with patch("subprocess.run") as mock_run:
        assert mgr.is_running() is False
        mock_run.assert_not_called()


def test_supervisor_is_running_false_when_not_alive(tmp_path: Path) -> None:
    mgr = _make_supervisor_manager(tmp_path)
    mgr.supervisor_conf_path.write_text("[supervisord]\n")
    # no PID file → is_alive() returns False before subprocess
    with patch("subprocess.run") as mock_run:
        assert mgr.is_running() is False
        mock_run.assert_not_called()


def test_supervisor_is_running_true_when_running_in_output(tmp_path: Path) -> None:
    import os
    mgr = _make_supervisor_manager(tmp_path)
    mgr.supervisor_conf_path.write_text("[supervisord]\n")
    mgr.supervisor_pid.write_text(str(os.getpid()))
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="test-bench:test-bench-web  RUNNING  pid 123\n")
        assert mgr.is_running() is True


def test_supervisor_is_running_false_when_no_running_in_output(tmp_path: Path) -> None:
    import os
    mgr = _make_supervisor_manager(tmp_path)
    mgr.supervisor_conf_path.write_text("[supervisord]\n")
    mgr.supervisor_pid.write_text(str(os.getpid()))
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="test-bench:test-bench-web  STOPPED\n")
        assert mgr.is_running() is False
