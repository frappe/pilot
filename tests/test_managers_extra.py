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
        workers=WorkerConfig(
            groups=[
                WorkerGroup(queues=["default"], count=1),
                WorkerGroup(queues=["short"], count=1),
                WorkerGroup(queues=["long"], count=1),
            ]
        ),
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
    from bench_cli.managers.process_managers.supervisor import SupervisorProcessManager

    bench = make_bench(tmp_path)
    (tmp_path / "config" / "supervisor").mkdir(parents=True, exist_ok=True)
    return SupervisorProcessManager(bench)


def test_supervisor_program_renders_working_dir(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition
    from bench_cli.managers.process_managers.supervisor import SupervisorRenderer

    pd = ProcessDefinition(
        name="web",
        command="/env/bin/python -m frappe.utils.bench_helper frappe serve",
        log_file=tmp_path / "logs" / "web.log",
        working_dir=Path("/sites"),
    )
    block = SupervisorRenderer("test-bench", tmp_path / "logs").render(pd)
    assert "directory=/sites" in block
    assert "command=/env/bin/python" in block
    assert "cd /sites" not in block


def test_supervisor_program_renders_env_vars(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition
    from bench_cli.managers.process_managers.supervisor import SupervisorRenderer

    pd = ProcessDefinition(
        name="admin",
        command="/env/bin/python -m admin.backend.server",
        log_file=tmp_path / "logs" / "admin.log",
        env={"PYTHONPATH": "/cli", "FOO": "bar"},
    )
    block = SupervisorRenderer("test-bench", tmp_path / "logs").render(pd)
    assert "environment=" in block
    assert 'PYTHONPATH="/cli"' in block
    assert 'FOO="bar"' in block
    assert "command=/env/bin/python" in block


def test_supervisor_program_no_prefix(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition
    from bench_cli.managers.process_managers.supervisor import SupervisorRenderer

    pd = ProcessDefinition(
        name="redis_cache",
        command="redis-server /config/redis_cache.conf",
        log_file=tmp_path / "logs" / "redis_cache.log",
    )
    block = SupervisorRenderer("test-bench", tmp_path / "logs").render(pd)
    assert "command=redis-server" in block
    assert "directory=" not in block
    assert "environment=" not in block


def test_supervisor_conf_has_group_section(tmp_path: Path) -> None:
    from bench_cli.managers.process_managers.supervisor import SupervisorRenderer

    conf = SupervisorRenderer("test-bench", tmp_path / "logs").conf([], tmp_path / "s.sock", tmp_path / "s.pid")
    assert "[group:test-bench]" in conf


def test_supervisor_conf_separates_admin_group(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition
    from bench_cli.managers.process_managers.supervisor import SupervisorRenderer

    fake_defs = [
        ProcessDefinition("web", "cmd_web", tmp_path / "logs" / "web.log"),
        ProcessDefinition("admin", "cmd_admin", tmp_path / "logs" / "admin.log"),
    ]
    conf = SupervisorRenderer("test-bench", tmp_path / "logs").conf(fake_defs, tmp_path / "s.sock", tmp_path / "s.pid")
    assert "[group:test-bench]" in conf
    assert "[group:test-bench-admin]" in conf
    # The workload group must not include the admin program.
    workload_line = [ln for ln in conf.splitlines() if ln.startswith("programs=")][0]
    assert "test-bench-admin" not in workload_line
    assert "test-bench-web" in workload_line


def test_supervisor_conf_has_unix_http_server(tmp_path: Path) -> None:
    from bench_cli.managers.process_managers.supervisor import SupervisorRenderer

    sock = tmp_path / "s.sock"
    conf = SupervisorRenderer("test-bench", tmp_path / "logs").conf([], sock, tmp_path / "s.pid")
    assert "[unix_http_server]" in conf
    assert f"file={sock}" in conf


def test_supervisor_conf_program_names_in_group(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition
    from bench_cli.managers.process_managers.supervisor import SupervisorRenderer

    fake_defs = [
        ProcessDefinition("web", "cmd_web", tmp_path / "logs" / "web.log"),
        ProcessDefinition("worker_default_1", "cmd_worker", tmp_path / "logs" / "w.log"),
    ]
    conf = SupervisorRenderer("test-bench", tmp_path / "logs").conf(fake_defs, tmp_path / "s.sock", tmp_path / "s.pid")
    assert "test-bench-web" in conf
    assert "test-bench-worker-default-1" in conf


def test_supervisor_conf_redis_gets_stop_timeout(tmp_path: Path) -> None:
    """The redis stop grace must reach the supervisor renderer, not just systemd
    (the consistency fix: stop_timeout lives on the definition now)."""
    from bench_cli.managers.process_manager import ProcessDefinition
    from bench_cli.managers.process_managers.supervisor import SupervisorRenderer

    fake_defs = [ProcessDefinition("redis_cache", "redis-server x.conf", tmp_path / "r.log", stop_timeout=300)]
    conf = SupervisorRenderer("test-bench", tmp_path / "logs").conf(fake_defs, tmp_path / "s.sock", tmp_path / "s.pid")
    assert "stopwaitsecs=300" in conf


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
    with patch("bench_cli.managers.process_managers.supervisor.AdminEnvManager"):
        with patch.object(mgr, "_prod_process_definitions", return_value=[]):
            mgr.write_config()
    assert mgr.supervisor_conf_path.exists()


def test_supervisor_conf_no_user_directive(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition
    from bench_cli.managers.process_managers.supervisor import SupervisorRenderer

    fake_defs = [ProcessDefinition("web", "cmd_web", tmp_path / "logs" / "web.log")]
    conf = SupervisorRenderer("test-bench", tmp_path / "logs").conf(fake_defs, tmp_path / "s.sock", tmp_path / "s.pid")
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
    from bench_cli.managers.process_managers.systemd import SystemdProcessManager

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


def test_systemd_unit_renders_working_dir(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition
    from bench_cli.managers.process_managers.systemd import SystemdRenderer

    pd = ProcessDefinition(
        name="web",
        command="/env/bin/python -m frappe.utils.bench_helper frappe serve",
        log_file=tmp_path / "logs" / "web.log",
        working_dir=Path("/sites"),
    )
    unit = SystemdRenderer("test-bench").render(pd)
    assert "WorkingDirectory=/sites" in unit
    assert "ExecStart=/env/bin/python" in unit
    assert "cd /sites" not in unit


def test_systemd_unit_renders_env_vars(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition
    from bench_cli.managers.process_managers.systemd import SystemdRenderer

    pd = ProcessDefinition(
        name="admin",
        command="/env/bin/python -m admin.backend.server",
        log_file=tmp_path / "logs" / "admin.log",
        env={"PYTHONPATH": "/cli", "FOO": "bar"},
    )
    unit = SystemdRenderer("test-bench").render(pd)
    assert "Environment=PYTHONPATH=/cli" in unit
    assert "Environment=FOO=bar" in unit
    assert "ExecStart=/env/bin/python" in unit


def test_systemd_unit_no_user_directive(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition
    from bench_cli.managers.process_managers.systemd import SystemdRenderer

    pd = ProcessDefinition(name="web", command="/env/bin/python serve", log_file=tmp_path / "logs" / "web.log")
    unit = SystemdRenderer("test-bench").render(pd)
    assert "User=" not in unit


def test_systemd_unit_part_of_target(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition
    from bench_cli.managers.process_managers.systemd import SystemdRenderer

    pd = ProcessDefinition(name="web", command="/env/bin/python serve", log_file=tmp_path / "logs" / "web.log")
    unit = SystemdRenderer("test-bench").render(pd)
    assert "PartOf=test-bench.target" in unit


def test_systemd_unit_redis_gets_stop_timeout(tmp_path: Path) -> None:
    """The redis stop grace reaches the systemd renderer from the definition."""
    from bench_cli.managers.process_manager import ProcessDefinition
    from bench_cli.managers.process_managers.systemd import SystemdRenderer

    pd = ProcessDefinition("redis_cache", "redis-server x.conf", tmp_path / "r.log", stop_timeout=300)
    unit = SystemdRenderer("test-bench").render(pd)
    assert "TimeoutStopSec=300" in unit


def test_systemd_target_wanted_by_default(tmp_path: Path) -> None:
    from bench_cli.managers.process_managers.systemd import SystemdRenderer

    target = SystemdRenderer("test-bench").target([])
    assert "WantedBy=default.target" in target


def test_systemd_generate_config_writes_unit_files(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition

    mgr = _make_systemd_manager(tmp_path)
    mgr.systemd_conf_dir.mkdir(parents=True, exist_ok=True)
    fake_defs = [ProcessDefinition("web", "/env/bin/python serve", tmp_path / "logs" / "web.log")]
    with patch("bench_cli.managers.admin_env_manager.AdminEnvManager"):
        with patch.object(mgr, "_prod_process_definitions", return_value=fake_defs):
            mgr.write_config()
    assert (mgr.systemd_conf_dir / "test-bench-web.service").exists()
    assert (mgr.systemd_conf_dir / "test-bench.target").exists()


def test_systemd_admin_socket_listens_on_internal_port(tmp_path: Path) -> None:
    from bench_cli.managers.process_managers.systemd import SystemdRenderer

    socket_unit = SystemdRenderer("test-bench").admin_socket(7001)
    assert "[Socket]" in socket_unit
    assert "ListenStream=127.0.0.1:7001" in socket_unit
    # Independent of the workload target so the admin survives `bench stop`.
    assert "WantedBy=default.target" in socket_unit
    assert "PartOf=" not in socket_unit


def test_systemd_admin_service_runs_gunicorn_with_idle_timeout(tmp_path: Path) -> None:
    mgr = _make_systemd_manager(tmp_path)
    service = mgr._admin_service_text()
    assert "admin.backend.wsgi:application" in service
    assert "Environment=BENCH_ADMIN_IDLE_TIMEOUT=60" in service
    assert "Requires=test-bench-admin.socket" in service
    assert "After=test-bench-admin.socket" in service
    # Re-activation is via the socket, not a systemd restart loop.
    assert "Restart=no" in service
    assert "KillMode=process" in service
    # Not PartOf the target — stopping the workload must not stop the admin.
    assert "PartOf=" not in service


def test_systemd_target_excludes_admin(tmp_path: Path) -> None:
    from bench_cli.managers.process_managers.systemd import SystemdRenderer

    # write_config feeds the target only workload unit names (admin excluded).
    target = SystemdRenderer("test-bench").target(["test-bench-web.service"])
    assert "test-bench-admin.socket" not in target
    assert "test-bench-admin.service" not in target
    assert "test-bench-web.service" in target


def test_systemd_generate_config_writes_admin_socket(tmp_path: Path) -> None:
    from bench_cli.managers.process_manager import ProcessDefinition

    mgr = _make_systemd_manager(tmp_path)
    mgr.systemd_conf_dir.mkdir(parents=True, exist_ok=True)
    fake_defs = [
        ProcessDefinition("web", "/env/bin/python serve", tmp_path / "logs" / "web.log"),
        ProcessDefinition("admin", "/env/bin/python -m admin", tmp_path / "logs" / "admin.log"),
    ]
    with patch("bench_cli.managers.admin_env_manager.AdminEnvManager"):
        with patch.object(mgr, "_prod_process_definitions", return_value=fake_defs):
            mgr.write_config()
    assert (mgr.systemd_conf_dir / "test-bench-admin.socket").exists()
    assert (mgr.systemd_conf_dir / "test-bench-admin.service").exists()
    assert (mgr.bench.config_path / "admin-gunicorn.conf.py").exists()


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


def test_supervisor_multiqueue_worker_name_has_no_commas(tmp_path: Path) -> None:
    """A worker group serving several queues must not produce a comma in the
    program name — commas break supervisor's `programs=` CSV (regression)."""
    from bench_cli.config.worker_config import WorkerConfig, WorkerGroup

    from bench_cli.managers.process_managers.supervisor import SupervisorRenderer

    mgr = _make_supervisor_manager(tmp_path)
    mgr.bench.config.workers = WorkerConfig(groups=[WorkerGroup(queues=["default", "short", "long"], count=1)])
    renderer = SupervisorRenderer("test-bench", mgr.bench.logs_path)
    conf = renderer.conf(mgr._prod_process_definitions(), mgr.supervisor_sock, mgr.supervisor_pid)
    workload_line = [ln for ln in conf.splitlines() if ln.startswith("programs=")][0]
    # Every program named in the group must exist as a [program:...] section.
    named = workload_line.split("=", 1)[1].split(",")
    for prog in named:
        assert f"[program:{prog}]" in conf, f"{prog} has no matching section"
    # The worker still serves all three queues via --queue.
    assert "--queue default,short,long" in conf


# ── ManagedProcessManager — shared lifecycle ───────────────────────────────


class _FakeProcessManager:
    """A ManagedProcessManager whose primitives just record their calls, so
    the inherited lifecycle (start/stop/restart/reload_workers) can be pinned
    once for every backend."""

    def __init__(self) -> None:
        from bench_cli.managers.process_managers.base import ManagedProcessManager

        self.calls: list[tuple[str, object]] = []
        self.prepared = 0
        self.generated = 0
        self._is_running = True

        fake = self

        class _Impl(ManagedProcessManager):
            def __init__(self) -> None:
                pass  # skip Bench wiring; the primitives below are pure recorders

            def write_config(self_impl) -> None:
                fake.generated += 1

            def install_config(self_impl) -> None:
                pass

            def reload_manager_config(self_impl) -> None:
                pass

            def ensure_ready(self_impl) -> None:
                fake.prepared += 1

            def apply_unit_action(self_impl, action, role) -> None:
                fake.calls.append((action, role))

            def are_units_running(self_impl, role) -> bool:
                return fake._is_running

            def _invalidate_assets_cache(self_impl) -> None:
                pass

        self.manager = _Impl()


def test_supervised_start_brings_up_admin_then_workload() -> None:
    from bench_cli.managers.process_managers.base import UnitGroup

    fake = _FakeProcessManager()
    fake.manager.start()
    assert fake.generated == 1
    assert fake.prepared == 1
    assert fake.calls == [("start", UnitGroup.ADMIN), ("start", UnitGroup.WORKLOAD)]


def test_supervised_stop_targets_workload_only() -> None:
    from bench_cli.managers.process_managers.base import UnitGroup

    fake = _FakeProcessManager()
    fake.manager.stop()
    assert fake.calls == [("stop", UnitGroup.WORKLOAD)]


def test_supervised_stop_admin_targets_admin_only() -> None:
    from bench_cli.managers.process_managers.base import UnitGroup

    fake = _FakeProcessManager()
    fake.manager.stop_admin()
    assert fake.calls == [("stop", UnitGroup.ADMIN)]


def test_supervised_reload_workers_web_only_restarts_web() -> None:
    from bench_cli.managers.process_managers.base import UnitGroup

    fake = _FakeProcessManager()
    fake.manager.reload_workers(web_only=True)
    assert fake.calls == [("restart", UnitGroup.WEB)]


def test_supervised_reload_workers_full_restarts_workload() -> None:
    from bench_cli.managers.process_managers.base import UnitGroup

    fake = _FakeProcessManager()
    fake.manager.reload_workers(web_only=False)
    assert fake.calls == [("restart", UnitGroup.WORKLOAD)]


def test_supervised_reload_workers_noop_when_not_running() -> None:
    fake = _FakeProcessManager()
    fake._is_running = False
    fake.manager.reload_workers()
    assert fake.calls == []
