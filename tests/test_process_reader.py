"""Unit tests for ProcessReader."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from admin.backend.readers.process_reader import ProcessReader


def make_reader(tmp_path: Path) -> ProcessReader:
    return ProcessReader(tmp_path)


# ── _parse_systemd_block ──────────────────────────────────────────────────────


def test_parse_systemd_block_running(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    block = "Id=test-bench-web.service\nActiveState=active\nMainPID=1234"
    with patch("admin.backend.readers.process_reader._get_process_stats", return_value=(1.0, 40.0)):
        info = reader._parse_systemd_block(block, "test-bench")
    assert info is not None
    assert info.name == "web"
    assert info.status == "running"
    assert info.pid == 1234


def test_parse_systemd_block_stopped(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    block = "Id=test-bench-web.service\nActiveState=inactive\nMainPID=0"
    info = reader._parse_systemd_block(block, "test-bench")
    assert info is not None
    assert info.status == "stopped"
    assert info.pid is None


def test_parse_systemd_block_failed(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    block = "Id=test-bench-worker.service\nActiveState=failed\nMainPID=0"
    info = reader._parse_systemd_block(block, "test-bench")
    assert info is not None
    assert info.status == "stopped"


def test_parse_systemd_block_not_a_service_returns_none(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    block = "Id=test-bench.target\nActiveState=active\nMainPID=0"
    assert reader._parse_systemd_block(block, "test-bench") is None


def test_parse_systemd_block_strips_bench_name_prefix(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    block = "Id=my-bench-worker-default-1.service\nActiveState=active\nMainPID=9999"
    with patch("admin.backend.readers.process_reader._get_process_stats", return_value=(None, None)):
        info = reader._parse_systemd_block(block, "my-bench")
    assert info is not None
    assert info.name == "worker-default-1"


# ── _read_from_systemd ────────────────────────────────────────────────────────


def test_read_from_systemd_no_units_returns_empty(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    systemd = MagicMock()
    systemd.bench.config.name = "test-bench"
    systemd.user_unit_dir.glob.return_value = []
    assert reader._read_from_systemd(systemd) == []


def test_read_from_systemd_parses_multiple_units(tmp_path: Path) -> None:
    unit_dir = tmp_path / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "test-bench-web.service").touch()
    (unit_dir / "test-bench-worker.service").touch()

    reader = make_reader(tmp_path)
    systemd = MagicMock()
    systemd.bench.config.name = "test-bench"
    systemd.user_unit_dir.glob.return_value = sorted(unit_dir.glob("test-bench-*.service"))
    systemd._systemctl.return_value = ["systemctl", "--user", "show"]
    systemd._systemctl_env.return_value = {}

    stdout = (
        "Id=test-bench-web.service\nActiveState=active\nMainPID=111\n\n"
        "Id=test-bench-worker.service\nActiveState=inactive\nMainPID=0"
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=stdout)
        with patch("admin.backend.readers.process_reader._get_process_stats", return_value=(1.2, 45.0)):
            result = reader._read_from_systemd(systemd)

    assert len(result) == 2
    web = next(p for p in result if p.name == "web")
    worker = next(p for p in result if p.name == "worker")
    assert web.status == "running"
    assert web.pid == 111
    assert web.cpu_percent == 1.2
    assert worker.status == "stopped"
    assert worker.pid is None
    assert worker.cpu_percent is None


# ── _parse_supervisorctl_line ─────────────────────────────────────────────────


def test_parse_supervisorctl_running_with_pid_and_uptime(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    line = "test-bench:test-bench-web  RUNNING  pid 5678, uptime 0:01:23"
    with patch("admin.backend.readers.process_reader._get_process_stats", return_value=(0.5, 30.0)):
        info = reader._parse_supervisorctl_line(line, "test-bench")
    assert info is not None
    assert info.name == "web"
    assert info.status == "running"
    assert info.pid == 5678
    assert info.uptime == "0:01:23"


def test_parse_supervisorctl_stopped(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    line = "test-bench:test-bench-web  STOPPED"
    info = reader._parse_supervisorctl_line(line, "test-bench")
    assert info is not None
    assert info.status == "stopped"
    assert info.pid is None
    assert info.uptime is None


def test_parse_supervisorctl_fatal_is_stopped(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    line = "test-bench:test-bench-worker  FATAL  Exited too quickly"
    info = reader._parse_supervisorctl_line(line, "test-bench")
    assert info is not None
    assert info.status == "stopped"


def test_parse_supervisorctl_malformed_returns_none(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    assert reader._parse_supervisorctl_line("not a valid line", "test-bench") is None


def test_parse_supervisorctl_strips_bench_name_prefix(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    line = "my-bench:my-bench-worker-default-1  RUNNING  pid 42, uptime 1:00:00"
    with patch("admin.backend.readers.process_reader._get_process_stats", return_value=(None, None)):
        info = reader._parse_supervisorctl_line(line, "my-bench")
    assert info is not None
    assert info.name == "worker-default-1"


# ── _read_from_supervisor ─────────────────────────────────────────────────────


def test_read_from_supervisor_parses_output(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    supervisor = MagicMock()
    supervisor.bench.config.name = "test-bench"
    supervisor._supervisorctl.return_value = ["supervisorctl", "-c", "/path/to/supervisord.conf"]

    stdout = (
        "test-bench:test-bench-web  RUNNING  pid 111, uptime 0:05:00\n"
        "test-bench:test-bench-worker  STOPPED\n"
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=stdout)
        with patch("admin.backend.readers.process_reader._get_process_stats", return_value=(0.5, 30.0)):
            result = reader._read_from_supervisor(supervisor)

    assert len(result) == 2
    web = next(p for p in result if p.name == "web")
    assert web.status == "running"
    assert web.pid == 111


def test_read_from_supervisor_skips_blank_lines(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    supervisor = MagicMock()
    supervisor.bench.config.name = "test-bench"
    supervisor._supervisorctl.return_value = ["supervisorctl", "-c", "/conf"]

    stdout = "\ntest-bench:test-bench-web  STOPPED\n\n"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=stdout)
        result = reader._read_from_supervisor(supervisor)

    assert len(result) == 1


# ── _read_from_pids ───────────────────────────────────────────────────────────


def test_read_from_pids_no_pids_dir_returns_empty(tmp_path: Path) -> None:
    assert make_reader(tmp_path)._read_from_pids() == []


def test_read_from_pids_running_process(tmp_path: Path) -> None:
    pids_dir = tmp_path / "pids"
    pids_dir.mkdir()
    (pids_dir / "web.pid").write_text(str(os.getpid()))

    with patch("admin.backend.readers.process_reader._get_process_stats", return_value=(2.0, 50.0)):
        result = make_reader(tmp_path)._read_from_pids()

    assert len(result) == 1
    assert result[0].name == "web"
    assert result[0].status == "running"
    assert result[0].pid == os.getpid()
    assert result[0].cpu_percent == 2.0


def test_read_from_pids_stopped_process(tmp_path: Path) -> None:
    pids_dir = tmp_path / "pids"
    pids_dir.mkdir()
    (pids_dir / "worker.pid").write_text("999999")  # non-existent PID

    result = make_reader(tmp_path)._read_from_pids()

    assert len(result) == 1
    assert result[0].status == "stopped"
    assert result[0].cpu_percent is None


def test_read_from_pids_malformed_pid_file_is_unknown(tmp_path: Path) -> None:
    pids_dir = tmp_path / "pids"
    pids_dir.mkdir()
    (pids_dir / "broken.pid").write_text("not-a-number")

    result = make_reader(tmp_path)._read_from_pids()

    assert len(result) == 1
    assert result[0].status == "unknown"
    assert result[0].pid is None


# ── read_all routing ──────────────────────────────────────────────────────────


def _patch_managers(systemd_running: bool, supervisor_running: bool):
    mock_systemd = MagicMock()
    mock_systemd.is_running.return_value = systemd_running
    mock_supervisor = MagicMock()
    mock_supervisor.is_running.return_value = supervisor_running
    return (
        patch("bench_cli.config.bench_config.BenchConfig.from_file"),
        patch("bench_cli.core.bench.Bench"),
        patch("bench_cli.managers.systemd_process_manager.SystemdProcessManager", return_value=mock_systemd),
        patch("bench_cli.managers.supervisor_process_manager.SupervisorProcessManager", return_value=mock_supervisor),
        mock_systemd,
        mock_supervisor,
    )


def test_read_all_routes_to_systemd_when_running(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    p_cfg, p_bench, p_systemd, p_supervisor, mock_systemd, _ = _patch_managers(True, False)
    with p_cfg, p_bench, p_systemd, p_supervisor:
        with patch.object(reader, "_read_from_systemd", return_value=[]) as mock_read:
            reader.read_all()
    mock_read.assert_called_once_with(mock_systemd)


def test_read_all_skips_supervisor_when_systemd_running(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    p_cfg, p_bench, p_systemd, p_supervisor, _, mock_supervisor = _patch_managers(True, False)
    with p_cfg, p_bench, p_systemd, p_supervisor:
        with patch.object(reader, "_read_from_systemd", return_value=[]):
            reader.read_all()
    mock_supervisor.is_running.assert_not_called()


def test_read_all_routes_to_supervisor_when_systemd_not_running(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    p_cfg, p_bench, p_systemd, p_supervisor, _, mock_supervisor = _patch_managers(False, True)
    with p_cfg, p_bench, p_systemd, p_supervisor:
        with patch.object(reader, "_read_from_supervisor", return_value=[]) as mock_read:
            reader.read_all()
    mock_read.assert_called_once_with(mock_supervisor)


def test_read_all_falls_back_to_pids_when_no_manager_running(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    p_cfg, p_bench, p_systemd, p_supervisor, _, _ = _patch_managers(False, False)
    with p_cfg, p_bench, p_systemd, p_supervisor:
        with patch.object(reader, "_read_from_pids", return_value=[]) as mock_read:
            reader.read_all()
    mock_read.assert_called_once()
