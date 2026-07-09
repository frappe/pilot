from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from pilot.commands.diagnostics import DiagnosticsCommand
from pilot.config.app_config import AppConfig
from pilot.config.bench_config import BenchConfig
from pilot.config.mariadb_config import MariaDBConfig
from pilot.config.redis_config import RedisConfig
from pilot.config.worker_config import WorkerConfig, WorkerGroup
from pilot.core.bench import Bench
from pilot.core.diagnostics import DiagnosticReport, DiagnosticRunner


def make_bench(tmp_path: Path) -> Bench:
    config = BenchConfig(
        name="diag-bench",
        python_version="3.14",
        apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(groups=[WorkerGroup(queues=["default"], count=1)]),
    )
    return Bench(config, tmp_path)


def make_initialized_bench(tmp_path: Path) -> Bench:
    bench = make_bench(tmp_path)
    for path in [bench.apps_path, bench.sites_path, bench.config_path, bench.python.parent, bench.pids_path]:
        path.mkdir(parents=True, exist_ok=True)
    (bench.path / "bench.toml").write_text("[bench]\nname = \"diag-bench\"\n")
    (bench.sites_path / "common_site_config.json").write_text("{}")
    (bench.config_path / "Procfile").write_text("web: run\n")
    bench.python.write_text("")
    return bench


def test_diagnostics_reports_missing_bench_paths(tmp_path: Path, monkeypatch) -> None:
    bench = make_bench(tmp_path)
    monkeypatch.setattr(DiagnosticRunner, "_resource_checks", lambda self: [])
    monkeypatch.setattr(DiagnosticRunner, "_process_checks", lambda self: [])
    monkeypatch.setattr(DiagnosticRunner, "_redis_checks", lambda self: [])
    monkeypatch.setattr(DiagnosticRunner, "_database_checks", lambda self: [])
    monkeypatch.setattr(DiagnosticRunner, "_worker_checks", lambda self: [])
    monkeypatch.setattr(DiagnosticRunner, "_dependency_checks", lambda self: [])

    checks = DiagnosticRunner(bench).run()

    assert any(check.name == "bench.toml" and check.status == "fail" for check in checks)
    assert any(check.name == "python env" and check.status == "fail" for check in checks)
    assert any(check.name == "sites" and check.status == "fail" for check in checks)


def test_diagnostics_marks_ports_reachable(tmp_path: Path, monkeypatch) -> None:
    bench = make_initialized_bench(tmp_path)
    monkeypatch.setattr(DiagnosticRunner, "_resource_checks", lambda self: [])
    monkeypatch.setattr(DiagnosticRunner, "_process_checks", lambda self: [])
    monkeypatch.setattr(DiagnosticRunner, "_worker_checks", lambda self: [])
    monkeypatch.setattr(DiagnosticRunner, "_tcp_open", lambda self, host, port: port in {13000, 11000})

    checks = DiagnosticRunner(bench).run()

    cache = next(check for check in checks if check.group == "redis" and check.name == "cache")
    database = next(check for check in checks if check.group == "database")
    assert cache.status == "ok"
    assert database.status == "fail"


def test_diagnostics_worker_pid_check(tmp_path: Path, monkeypatch) -> None:
    bench = make_initialized_bench(tmp_path)
    (bench.pids_path / "worker_default_1.pid").write_text("123")
    monkeypatch.setattr(DiagnosticRunner, "_pid_alive", lambda self, path: True)

    checks = DiagnosticRunner(bench)._worker_checks()

    assert checks[0].status == "ok"
    assert checks[0].detail == "1/1 worker pid files are live"


def test_diagnostics_validates_site_configs(tmp_path: Path) -> None:
    bench = make_initialized_bench(tmp_path)
    site = bench.sites_path / "site.localhost"
    site.mkdir()
    (site / "site_config.json").write_text("{")

    checks = DiagnosticRunner(bench)._site_checks()

    assert any(check.name == "site count" and check.status == "ok" for check in checks)
    assert any(check.name == "site.localhost" and check.status == "fail" for check in checks)


def test_diagnostics_warns_when_no_sites_exist(tmp_path: Path) -> None:
    bench = make_initialized_bench(tmp_path)

    checks = DiagnosticRunner(bench)._site_checks()

    assert checks[0].status == "warn"
    assert "no sites" in checks[0].detail


def test_diagnostics_checks_chromium_dependency(tmp_path: Path, monkeypatch) -> None:
    bench = make_initialized_bench(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/chromium" if name == "chromium" else None)

    check = DiagnosticRunner(bench)._chromium_check()

    assert check.status == "ok"
    assert check.detail == "/usr/bin/chromium"


def test_diagnostics_warns_when_chromium_missing(tmp_path: Path, monkeypatch) -> None:
    bench = make_initialized_bench(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: None)

    check = DiagnosticRunner(bench)._chromium_check()

    assert check.status == "warn"
    assert "PDF generation" in check.hint


def test_diagnostics_cpu_load_warns_under_pressure(tmp_path: Path, monkeypatch) -> None:
    bench = make_initialized_bench(tmp_path)
    monkeypatch.setattr("os.getloadavg", lambda: (2.5, 2.0, 1.0))
    monkeypatch.setattr("os.cpu_count", lambda: 2)

    check = DiagnosticRunner(bench)._cpu_check()

    assert check.status == "warn"


def test_diagnostics_memory_parser_ignores_malformed_lines(tmp_path: Path, monkeypatch) -> None:
    bench = make_initialized_bench(tmp_path)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("broken\nMemTotal:\nMemFree: nope kB\nMemAvailable: 2048 kB\n")
    monkeypatch.setattr(DiagnosticRunner, "_meminfo_path", lambda self: meminfo)

    assert DiagnosticRunner(bench)._linux_memory() == {"available": 2048}


def test_diagnostics_memory_parser_skips_without_available_memory(tmp_path: Path, monkeypatch) -> None:
    bench = make_initialized_bench(tmp_path)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 4096 kB\n")
    monkeypatch.setattr(DiagnosticRunner, "_meminfo_path", lambda self: meminfo)

    assert DiagnosticRunner(bench)._memory_check().status == "skip"


def test_diagnostic_report_json(tmp_path: Path) -> None:
    bench = make_initialized_bench(tmp_path)
    check = DiagnosticRunner(bench)._path_check("bench", "bench.toml", bench.path / "bench.toml", "")
    payload = json.loads(DiagnosticReport(bench.config.name, [check]).to_json())

    assert payload["bench"] == "diag-bench"
    assert payload["checks"][0]["status"] == "ok"


def test_diagnostics_command_prints_json(tmp_path: Path, monkeypatch, capsys) -> None:
    bench = make_initialized_bench(tmp_path)
    monkeypatch.setattr(DiagnosticRunner, "run", lambda self: [])

    DiagnosticsCommand(bench, json_output=True).run()

    assert json.loads(capsys.readouterr().out)["bench"] == "diag-bench"


def test_diagnostics_command_prints_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    bench = make_initialized_bench(tmp_path)
    check = MagicMock(group="bench", status="ok", name="bench.toml", detail="present", hint="")
    monkeypatch.setattr(DiagnosticRunner, "run", lambda self: [check])

    DiagnosticsCommand(bench).run()

    output = capsys.readouterr().out
    assert "Diagnostics for diag-bench" in output
    assert "Result: all checks passed" in output
