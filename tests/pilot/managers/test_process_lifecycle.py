from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from pilot.core.app.processes import AppProcessError
from pilot.core.bench.processes import BenchProcesses
from pilot.core.bench.setup import ProductionSetup
from pilot.exceptions import BenchError
from pilot.managers.processes import supervisor as supervisor_module
from pilot.managers.processes import systemd as systemd_module
from pilot.managers.processes.base import UnitGroup
from pilot.managers.processes.local import ProcessManager
from pilot.managers.processes.supervisor import SupervisorProcessManager
from pilot.managers.processes.systemd import SystemdProcessManager
from tests.pilot.managers.test_managers_extra import make_bench


@pytest.fixture
def bench(tmp_path: Path):
    bench = make_bench(tmp_path)
    bench.config.production.enabled = True
    return bench


@pytest.mark.parametrize(("is_running", "action"), [(True, "start"), (False, "stop")])
def test_apply_installs_the_process_set_and_keeps_the_workload_state(bench, is_running, action) -> None:
    manager = MagicMock()
    manager.is_running.return_value = is_running
    with patch.object(ProcessManager, "for_bench", return_value=manager):
        BenchProcesses(bench).apply()

    manager.write_config.assert_called_once_with()
    manager.install_config.assert_called_once_with()
    manager.reload_manager_config.assert_called_once_with()
    # start leaves running processes alone, so only an added one starts; stop undoes
    # supervisor starting a newly loaded group on a stopped bench.
    manager.apply_unit_action.assert_called_once_with(action, UnitGroup.WORKLOAD)
    manager.restart_processes.assert_not_called()


def test_apply_leaves_a_development_bench_alone(bench) -> None:
    bench.config.production.enabled = False
    manager = MagicMock()
    with patch.object(ProcessManager, "for_bench", return_value=manager):
        BenchProcesses(bench).apply()

    manager.write_config.assert_not_called()


def _systemctl_calls(manager: SystemdProcessManager, is_workload_running: bool, active: set[str]) -> list[list[str]]:
    calls = []

    def run(argv, **kwargs):
        calls.append(argv[2:])
        is_active = argv[2] == "is-active" and argv[3] in active
        return subprocess.CompletedProcess(argv, 0 if is_active else 3)

    manager.changed_sockets = ("test-bench-web.socket",)
    with (
        patch.object(systemd_module.subprocess, "run", side_effect=run),
        patch.object(systemd_module, "run_command", side_effect=lambda argv, **kwargs: calls.append(argv[2:])),
        patch.object(manager, "is_running", return_value=is_workload_running),
    ):
        manager._activate_sockets({})
    return calls


def test_web_hands_its_port_to_the_socket_and_starts_again(bench) -> None:
    manager = SystemdProcessManager(bench)

    calls = _systemctl_calls(manager, is_workload_running=True, active={"test-bench-web.service"})

    assert calls[1:] == [
        ["stop", "test-bench-web.service"],
        ["restart", "test-bench-web.socket"],
        ["start", "test-bench-web.service"],
    ]
    assert manager.changed_sockets == ()


def test_web_socket_waits_for_the_workload_to_start(bench) -> None:
    calls = _systemctl_calls(SystemdProcessManager(bench), is_workload_running=False, active=set())

    assert calls[1:] == [["stop", "test-bench-web.service"]]


def test_inactive_web_is_left_to_its_socket(bench) -> None:
    calls = _systemctl_calls(SystemdProcessManager(bench), is_workload_running=True, active=set())

    assert calls[1:] == [["stop", "test-bench-web.service"], ["restart", "test-bench-web.socket"]]


@pytest.mark.parametrize(
    ("is_running", "argv"),
    [
        (
            True,
            [
                "restart",
                "test-bench-web.service",
                "test-bench-redis_cache.service",
                "test-bench-redis_queue.service",
            ],
        ),
        (False, ["restart", "test-bench.target"]),
    ],
)
def test_restart_keeps_the_web_socket_open(bench, is_running, argv) -> None:
    """A target restart closes the socket too, which resets the requests waiting in it."""
    manager = SystemdProcessManager(bench)
    names = ["web", "admin", "redis_cache", "redis_queue"]
    definitions = [MagicMock() for _ in names]
    for definition, name in zip(definitions, names, strict=True):
        definition.name = name
    calls = []
    with (
        patch.object(SystemdProcessManager, "process_definitions", new_callable=PropertyMock, return_value=definitions),
        patch.object(manager, "is_running", return_value=is_running),
        patch.object(systemd_module, "run_command", side_effect=lambda argv, **kwargs: calls.append(argv[2:])),
    ):
        manager.restart()

    assert calls == [argv]


_AVAIL = (
    "test-bench:test-bench-web           in use    auto      999:999\n"
    "test-bench-admin                    in use    auto      999:999\n"
)


@pytest.mark.parametrize(
    ("avail", "targets"),
    [
        (_AVAIL, ["test-bench:*"]),
        (
            _AVAIL
            + "test-bench-apps:test-bench-mail   in use    auto      999:999\n"
            + "test-bench-redis:test-bench-redis_cache   in use    auto      999:999\n",
            ["test-bench:*", "test-bench-apps:*", "test-bench-redis:*"],
        ),
    ],
    ids=["before-redis-group", "every-group"],
)
def test_supervisor_targets_only_loaded_groups(bench, avail, targets) -> None:
    manager = SupervisorProcessManager(bench)
    with patch.object(supervisor_module.subprocess, "run", return_value=MagicMock(stdout=avail)):
        assert manager.get_targets(UnitGroup.WORKLOAD) == targets


def test_supervisor_shutdown_waits_for_the_daemon_to_exit(bench) -> None:
    manager = SupervisorProcessManager(bench)
    alive = iter([True, True, True, False])
    with (
        patch.object(manager, "is_alive", side_effect=lambda: next(alive)),
        patch.object(supervisor_module, "run_command") as run,
        patch.object(supervisor_module.time, "sleep") as sleep,
    ):
        manager.shutdown()

    assert run.call_args.args[0][-1] == "shutdown"
    assert sleep.call_count == 2


def test_supervisor_shutdown_fails_when_the_daemon_outlives_the_timeout(bench) -> None:
    manager = SupervisorProcessManager(bench)
    clock = iter([0, supervisor_module._SHUTDOWN_TIMEOUT + 1])
    with (
        patch.object(manager, "is_alive", return_value=True),
        patch.object(supervisor_module, "run_command"),
        patch.object(supervisor_module.time, "monotonic", side_effect=lambda: next(clock)),
        pytest.raises(BenchError, match="did not exit"),
    ):
        manager.shutdown()


@pytest.mark.parametrize(("running", "expected"), [("supervisor", "supervisor"), (None, "systemd")])
def test_switch_tears_down_the_running_manager_first(bench, running, expected) -> None:
    with (
        patch.object(SystemdProcessManager, "is_running", return_value=running == "systemd"),
        patch.object(SupervisorProcessManager, "is_running", return_value=running == "supervisor"),
        patch.object(SystemdProcessManager, "is_configured", return_value=True),
        patch.object(SupervisorProcessManager, "is_configured", return_value=True),
    ):
        assert ProductionSetup(bench)._installed_manager() == expected


@pytest.mark.parametrize(
    ("target", "old", "declaration", "error"),
    [
        ("systemd", "supervisor", 'command = "server"\n', AppProcessError),
        # systemd holds this value, supervisord cannot.
        ("supervisor", "systemd", 'command = ["server"]\nenv = { MESSAGE = "it\'s \\"here\\"" }\n', BenchError),
    ],
    ids=["broken-declaration", "value-the-target-cannot-hold"],
)
def test_the_target_config_must_build_before_the_old_manager_stops(bench, target, old, declaration, error) -> None:
    app = bench.apps_path / "mail"
    (app / ".git").mkdir(parents=True)
    (app / "pyproject.toml").write_text("[tool.pilot.processes.server]\n" + declaration)
    bench.config.production.process_manager = target
    setup = ProductionSetup(bench, process_manager=target)
    preflight = ("_require_linux", "_check_sudo_available", "_resolve_target", "_require_production_inputs")
    steps = (*preflight, "_check_admin_domain", "_register_admin_domain", "_rollback_admin_domain")
    with (
        patch.multiple(ProductionSetup, **dict.fromkeys(steps, MagicMock())),
        patch.object(ProductionSetup, "_installed_manager", return_value=old),
        patch.object(ProductionSetup, "_migrate_from") as migrate,
        patch.object(bench.config, "validate"),
        patch.object(BenchProcesses, "active_apps", new_callable=PropertyMock, return_value={"mail"}),
        pytest.raises(error),
    ):
        setup.run()

    migrate.assert_not_called()


def test_systemd_start_links_units_an_upgrade_added(bench) -> None:
    manager = SystemdProcessManager(bench)
    with (
        patch.object(manager, "write_config"),
        patch.object(manager, "install_config") as install,
        patch.object(manager, "apply_unit_action"),
    ):
        manager.start_workload()

    install.assert_called_once_with()


def test_settings_restart_reopens_the_web_socket_on_its_new_port(bench) -> None:
    from pilot.core.bench.settings import _restart_systemd

    manager = SystemdProcessManager(bench)
    calls = []
    with (
        patch.object(manager, "is_running", return_value=True),
        patch.object(manager, "link_units", side_effect=lambda: calls.append("link")),
        patch.object(manager, "reopen_sockets", side_effect=lambda: calls.append("reopen")),
        patch("pilot.core.bench.settings.subprocess.run", side_effect=lambda argv, **kwargs: calls.append(argv[2])),
    ):
        assert _restart_systemd(manager)

    assert calls == ["link", "daemon-reload", "reopen", "restart"]


def test_supervisor_finds_redis_in_the_workload_group_of_an_old_layout(bench) -> None:
    manager = SupervisorProcessManager(bench)
    avail = "test-bench:test-bench-web  in use  auto  999:999\ntest-bench:test-bench-redis-cache  in use  auto  999:999\n"
    redis = MagicMock(app=None)
    redis.name = "redis_cache"
    with (
        patch.object(supervisor_module.subprocess, "run", return_value=MagicMock(stdout=avail)),
        patch.object(supervisor_module, "run_command") as run,
    ):
        manager.restart_processes([redis])

    assert run.call_args.args[0][-1] == "test-bench:test-bench-redis-cache"
