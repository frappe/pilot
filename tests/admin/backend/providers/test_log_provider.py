from __future__ import annotations

from pathlib import Path

import pytest

from admin.backend.providers.logs import LogProvider, _read_tail_text
from pilot.managers.processes.sandbox import WORKLOAD_LOGS


def test_returns_full_content_when_file_smaller_than_block(tmp_path: Path) -> None:
    path = tmp_path / "small.log"
    path.write_text("a\nb\nc\n")
    assert _read_tail_text(path, min_lines=10) == "a\nb\nc\n"


def test_returns_empty_string_for_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.log"
    path.write_text("")
    assert _read_tail_text(path, min_lines=5) == ""


def test_grows_window_until_min_lines_found(tmp_path: Path) -> None:
    path = tmp_path / "big.log"
    path.write_text("".join(f"line-{i}\n" for i in range(1000)))

    text = _read_tail_text(path, min_lines=5, block_size=16)

    assert text.count("\n") >= 5
    assert text.splitlines()[-1] == "line-999"
    assert len(text) < path.stat().st_size


def test_never_reads_more_than_the_file_size(tmp_path: Path) -> None:
    path = tmp_path / "tiny.log"
    path.write_text("only-one-line\n")
    assert _read_tail_text(path, min_lines=1000, block_size=4) == "only-one-line\n"


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
