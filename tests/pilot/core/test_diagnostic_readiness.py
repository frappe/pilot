import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from pilot.config import BenchConfig
from pilot.core.bench.diagnostic_local import LocalDiagnostics
from pilot.core.bench.diagnostic_runtime import RuntimeDiagnostics
from pilot.core.bench.diagnostics import BenchDiagnostics, DiagnosticReport

GIB = 1024**3


@pytest.mark.parametrize(
    ("available", "total", "expected"),
    [(400 * 1024**2, GIB, "error"), (2 * GIB, 100 * GIB, "error"), (6 * GIB, 1000 * GIB, "ok")],
    ids=["absolute-reserve", "low-fraction-and-capacity", "large-filesystem-has-ample-space"],
)
def test_capacity_uses_user_available_space_with_conservative_thresholds(
    tmp_path, monkeypatch, available, total, expected
):
    stats = SimpleNamespace(
        f_frsize=4096,
        f_bsize=4096,
        f_bavail=available // 4096,
        f_bfree=total // 4096,
        f_blocks=total // 4096,
        f_files=1000,
        f_favail=900,
    )
    monkeypatch.setattr("os.statvfs", lambda path: stats)
    report = DiagnosticReport(str(tmp_path))
    LocalDiagnostics(report).check_capacity()
    disk = next(check for check in report.checks if check.code == "disk_capacity")
    assert disk.status == expected
    assert disk.details["available_bytes"] == available
    assert disk.reason == ("low_disk_space" if expected == "error" else "")


def test_inode_exhaustion_is_reported_with_plenty_of_disk_space(tmp_path, monkeypatch):
    stats = SimpleNamespace(
        f_frsize=4096,
        f_bsize=4096,
        f_bavail=20 * GIB // 4096,
        f_blocks=30 * GIB // 4096,
        f_files=10000,
        f_favail=10,
    )
    monkeypatch.setattr("os.statvfs", lambda path: stats)
    report = DiagnosticReport(str(tmp_path))
    LocalDiagnostics(report).check_capacity()
    checks = {check.code: check for check in report.checks}
    assert checks["disk_capacity"].status == "ok"
    assert checks["inode_capacity"].reason == "low_inodes"
    assert checks["inode_capacity"].details["available_inodes"] == 10


def test_inaccessible_filesystem_is_unavailable_not_healthy(tmp_path, monkeypatch):
    def inaccessible(path):
        raise PermissionError

    monkeypatch.setattr("os.statvfs", inaccessible)
    report = DiagnosticReport(str(tmp_path))
    LocalDiagnostics(report).check_capacity()
    assert {check.status for check in report.checks} == {"unavailable"}
    assert report.to_dict()["ok"] is False


def test_maintenance_uses_common_settings_and_site_override_without_writes(tmp_path):
    root = tmp_path / "bench"
    root.mkdir()
    BenchConfig.from_flat("demo", {"db_type": "sqlite"}).write(root)
    sites = root / "sites"
    sites.mkdir()
    (sites / "common_site_config.json").write_text(
        '{"db_type":"sqlite","maintenance_mode":1,"pause_scheduler":1}'
    )
    for name, overrides in (
        ("inherited.local", {}),
        ("online.local", {"maintenance_mode": 0, "pause_scheduler": 0}),
    ):
        site = sites / name
        site.mkdir()
        (site / "site_config.json").write_text(json.dumps({"db_name": "demo", **overrides}))
    original = {str(path): path.read_bytes() for path in sites.rglob("*.json")}
    report = BenchDiagnostics(root, offline=True).run()
    states = {
        (check.code, check.target): check.status
        for check in report.checks
        if check.code in ("maintenance_mode", "scheduler_paused")
    }
    assert states == {
        ("maintenance_mode", "inherited.local"): "error",
        ("scheduler_paused", "inherited.local"): "error",
        ("maintenance_mode", "online.local"): "ok",
        ("scheduler_paused", "online.local"): "ok",
    }
    assert {str(path): path.read_bytes() for path in sites.rglob("*.json")} == original


@contextmanager
def worker_process(root: Path, role: str, cwd: Path):
    for directory in (root / "frappe", root / "frappe" / "utils"):
        directory.mkdir(exist_ok=True, parents=True)
        (directory / "__init__.py").touch()
    (root / "frappe" / "utils" / "bench_helper.py").write_text(
        "import time\nprint('ready', flush=True)\ntime.sleep(30)\n"
    )
    arguments = [sys.executable, "-m", "frappe.utils.bench_helper", "frappe", role]
    if role == "worker":
        arguments.extend(["--queue", "default,short,long"])
    process = subprocess.Popen(
        arguments, cwd=cwd, env={**os.environ, "PYTHONPATH": str(root)}, stdout=subprocess.PIPE, text=True
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        yield process
    finally:
        process.terminate()
        process.wait(timeout=2)
        process.stdout.close()


def make_process_bench(tmp_path):
    root = tmp_path / "bench"
    root.mkdir()
    BenchConfig.from_flat("demo", {"db_type": "sqlite"}).write(root)
    (root / "sites").mkdir()
    (root / "pids").mkdir()
    return root


def test_worker_scheduler_processes_follow_real_pid_lifecycle(tmp_path):
    bench = make_process_bench(tmp_path)
    probe = RuntimeDiagnostics(DiagnosticReport(str(bench)), offline=False, timeout=2)
    with (
        worker_process(tmp_path / "modules", "worker", bench / "sites") as worker,
        worker_process(tmp_path / "modules", "schedule", bench / "sites") as scheduler,
    ):
        (bench / "pids" / "worker_default_short_long_1.pid").write_text(str(worker.pid))
        (bench / "pids" / "schedule.pid").write_text(str(scheduler.pid))
        healthy = probe.execute("processes", {"path": str(bench)})
        assert {(row["code"], row["status"]) for row in healthy["checks"]} == {
            ("worker_process", "ok"),
            ("scheduler_process", "ok"),
        }
    stopped = probe.execute("processes", {"path": str(bench)})
    assert {row["reason"] for row in stopped["checks"]} == {"process_not_running"}


def test_same_worker_command_in_another_bench_is_rejected(tmp_path):
    bench = make_process_bench(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    with worker_process(tmp_path / "modules", "worker", other) as worker:
        (bench / "pids" / "worker_default_short_long_1.pid").write_text(str(worker.pid))
        result = RuntimeDiagnostics(DiagnosticReport(str(bench)), offline=False, timeout=2).execute(
            "processes", {"path": str(bench)}
        )
    row = next(row for row in result["checks"] if row["code"] == "worker_process")
    assert row["status"] == "error" and row["reason"] == "process_identity_mismatch"


def test_lite_roles_are_explicitly_integrated_without_job_health_claims(tmp_path):
    bench = make_process_bench(tmp_path)
    runner = bench / "apps" / "frappe" / "frappe" / "runner.py"
    runner.parent.mkdir(parents=True)
    runner.touch()
    config = BenchConfig.read(bench)
    config.lite_mode.enabled = True
    config.write(bench)
    report = DiagnosticReport(str(bench))
    RuntimeDiagnostics(report, offline=False, timeout=2).check_processes()
    assert {check.code for check in report.checks} == {"worker_process", "scheduler_process"}
    assert {check.reason for check in report.checks} == {"integrated_lite"}
    assert report.to_dict()["complete"] is True
