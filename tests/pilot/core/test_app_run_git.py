from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.commands.apps.git import GitCommand
from pilot.config import AppConfig, BenchConfig
from pilot.core.app import App
from pilot.core.bench import Bench

_SECRET = "Authorization: Basic c2VjcmV0"
_BENCH_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [],
    "mariadb": {"root_password": "root"},
    "redis": {"cache_port": 13000, "queue_port": 11000},
}


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    bench = Bench(BenchConfig._from_dict(_BENCH_DATA), tmp_path)
    subprocess.run(["git", "init", "-q", str(bench.apps_path / "demo")], check=True)
    return bench


def _app(bench: Bench) -> App:
    return App(AppConfig(name="demo", repo="https://github.com/example/demo", branch="main"), bench)


def test_run_git_carries_the_stored_credential_only_in_the_environment(bench: Bench) -> None:
    app = _app(bench)

    with patch("pilot.integrations.git.auth_config_for", return_value={"http.extraHeader": _SECRET}):
        result = app.run_git("config", "--get", "http.extraHeader", capture_output=True)

    assert result.returncode == 0
    assert result.stdout.strip() == _SECRET
    assert _SECRET not in result.args
    assert _SECRET not in (app.path / ".git" / "config").read_text()


def test_run_git_returns_the_git_exit_code(bench: Bench) -> None:
    result = _app(bench).run_git("rev-parse", "--verify", "missing-ref", capture_output=True)

    assert result.returncode == 128


def test_git_command_exits_with_the_git_exit_code(bench: Bench) -> None:
    command = GitCommand(bench=bench, app="demo", args=("rev-parse", "--verify", "missing-ref"))

    with patch("pilot.integrations.git.auth_config_for", return_value={}), pytest.raises(SystemExit) as exit_info:
        command.run()

    assert exit_info.value.code == 128
