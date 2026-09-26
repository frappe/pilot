"""Tests for set-admin-password: explicit flag, TTY auto-generation, and non-TTY refusal."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilot.commands.sites.set_admin_password import SetAdminPasswordCommand
from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.exceptions import BenchError

_BENCH_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "redis": {"cache_port": 13000, "queue_port": 11000},
    "admin": {"enabled": True, "password": ""},
}


def _make_bench(tmp_path: Path) -> Bench:
    bench_dir = tmp_path / "bench"
    bench_dir.mkdir(parents=True, exist_ok=True)
    bench = Bench(BenchConfig._from_dict(_BENCH_DATA), bench_dir)
    bench.config.write(bench.path)
    return bench


def _run_cmd(bench: Bench, password: str | None = None) -> SetAdminPasswordCommand:
    cmd = SetAdminPasswordCommand(bench=bench, password=password)
    cmd.run()
    return cmd


def test_explicit_strong_password_is_saved(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path)
    _run_cmd(bench, password="Str0ng!pass")
    assert BenchConfig.read(bench.path).admin.verify_password("Str0ng!pass")


def test_explicit_weak_password_raises_before_saving(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path)
    with pytest.raises(BenchError, match="at least 8 characters"):
        _run_cmd(bench, password="weak")
    assert not BenchConfig.read(bench.path).admin.verify_password("weak")


def test_explicit_password_bypasses_tty_check(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    bench = _make_bench(tmp_path)
    _run_cmd(bench, password="Str0ng!pass")

    assert BenchConfig.read(bench.path).admin.verify_password("Str0ng!pass")


def test_prompted_password_with_redirected_stdout_succeeds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "Str0ng!pass")

    bench = _make_bench(tmp_path)
    _run_cmd(bench, password=None)

    assert BenchConfig.read(bench.path).admin.verify_password("Str0ng!pass")


def test_blank_prompt_with_redirected_stdout_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    bench = _make_bench(tmp_path)
    with pytest.raises(BenchError, match="--password"):
        _run_cmd(bench, password=None)


def test_blank_prompt_generates_a_password(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    bench = _make_bench(tmp_path)
    _run_cmd(bench, password=None)

    out = capsys.readouterr().out
    assert "Generated admin password:" in out
    prefix = "Generated admin password:"
    generated = next(line for line in out.splitlines() if prefix in line).split(prefix)[-1].strip()
    assert BenchConfig.read(bench.path).admin.verify_password(generated)


def test_blank_prompt_generated_password_is_stored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    bench = _make_bench(tmp_path)
    _run_cmd(bench, password=None)

    assert BenchConfig.read(bench.path).admin.password


def test_non_tty_stdout_raises_bench_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    bench = _make_bench(tmp_path)
    with pytest.raises(BenchError, match="--password"):
        _run_cmd(bench, password=None)


def test_non_tty_stdout_error_mentions_non_interactive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    bench = _make_bench(tmp_path)
    with pytest.raises(BenchError, match="non-interactive"):
        _run_cmd(bench, password=None)


def test_non_tty_stdout_leaves_password_unchanged(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    bench = _make_bench(tmp_path)
    with pytest.raises(BenchError):
        _run_cmd(bench, password=None)

    assert not BenchConfig.read(bench.path).admin.password


def test_tty_stdout_with_blank_input_generates_password(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    bench = _make_bench(tmp_path)
    _run_cmd(bench, password=None)

    out = capsys.readouterr().out
    assert "Generated admin password:" in out
    assert BenchConfig.read(bench.path).admin.password
