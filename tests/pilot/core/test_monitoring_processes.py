from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pilot.core.server.monitoring_processes import ProcessResolver
from pilot.managers.processes.supervisor import SupervisorProcessManager
from tests.pilot.managers.test_managers_extra import make_bench


def _production_bench(tmp_path: Path, manager: str):
    bench = make_bench(tmp_path)
    bench.config.production.enabled = True
    bench.config.production.process_manager = manager
    return bench


def test_systemd_units_are_read_from_the_bench_services_dir(tmp_path: Path, monkeypatch) -> None:
    bench = _production_bench(tmp_path, "systemd")
    services = bench.config_path / "services"
    services.mkdir(parents=True)
    for unit in ("test-bench-web.service", "test-bench-admin.service", "test-bench.target"):
        (services / unit).write_text("")
    monkeypatch.setattr(
        "pilot.core.server.monitoring_processes.run_command",
        lambda argv, **_: SimpleNamespace(stdout=b"MainPID=4242\n"),
    )

    assert ProcessResolver(bench).resolve() == {"test-bench-web.service": 4242}


def test_a_stopped_supervisor_bench_reports_no_processes(tmp_path: Path, monkeypatch) -> None:
    bench = _production_bench(tmp_path, "supervisor")
    monkeypatch.setattr(SupervisorProcessManager, "is_alive", lambda self: False)

    def no_daemon(argv, **_):
        raise AssertionError("supervisorctl must not run without a daemon")

    monkeypatch.setattr("pilot.core.server.monitoring_processes.run_command", no_daemon)

    assert ProcessResolver(bench).resolve() == {}
