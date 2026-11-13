from __future__ import annotations

from pathlib import Path

import pytest

from admin.backend.providers.server.logs import LogProvider
from pilot.managers.processes.sandbox import WORKLOAD_LOGS


def test_info_lines_on_stderr_are_not_errors(tmp_path: Path) -> None:
    log = tmp_path / "web.log.error.log"
    log.write_text("2026-11-12 13:21:04,309 INFO uvicorn.error Started server process [36887]\n")
    assert not LogProvider.has_recent_errors(log)

    log.write_text(log.read_text() + "Traceback (most recent call last):\n")
    assert LogProvider.has_recent_errors(log)


def _write_log(bench_root: Path, relative: str, content: str) -> None:
    path = bench_root / "logs" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_lists_only_the_logs_folder_without_a_workload_folder(tmp_path: Path) -> None:
    _write_log(tmp_path, "web.log", "web\n")

    assert [info.filename for info in LogProvider(tmp_path).get_all()] == ["web.log"]


def test_lists_and_tails_a_workload_log(tmp_path: Path) -> None:
    _write_log(tmp_path, "web.log", "web\n")
    _write_log(tmp_path, f"{WORKLOAD_LOGS}/frappe.log", "one\ntwo\n")
    provider = LogProvider(tmp_path)

    listed = provider.get_all()

    assert [info.filename for info in listed] == ["frappe.log", "web.log"]
    assert listed[0].line_count == 2
    assert provider.tail_file("frappe.log") == ["one", "two"]


def test_pilot_log_wins_over_a_workload_log_with_the_same_name(tmp_path: Path) -> None:
    _write_log(tmp_path, "admin.log", "pilot\n")
    _write_log(tmp_path, f"{WORKLOAD_LOGS}/admin.log", "workload\n")
    provider = LogProvider(tmp_path)

    assert [info.filename for info in provider.get_all()] == ["admin.log"]
    assert provider.tail_file("admin.log") == ["pilot"]


def test_refuses_a_workload_symlink_that_points_outside(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench"
    secret = tmp_path / "secret.log"
    secret.write_text("secret\n")
    workload_dir = bench_root / "logs" / WORKLOAD_LOGS
    workload_dir.mkdir(parents=True)
    (workload_dir / "leak.log").symlink_to(secret)
    provider = LogProvider(bench_root)

    assert provider.get_all() == []
    with pytest.raises(ValueError):
        provider.tail_file("leak.log")
