"""App-declared processes are registered into prod_process_definitions, fault-isolated."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pilot.exceptions import BenchError
from pilot.managers.processes.definitions import ProcessDefinitionBuilder
from tests.pilot.commands.test_commands import make_bench


def _make_app(bench, name: str, pyproject: str) -> None:
    app_dir = bench.apps_path / name
    (app_dir / ".git").mkdir(parents=True)
    (app_dir / "pyproject.toml").write_text(pyproject)


def _builder(bench) -> ProcessDefinitionBuilder:
    return ProcessDefinitionBuilder(bench, Path("/usr/bin/python3"), watch_admin_js=False)


_GOOD = '[tool.pilot.background_processes.stalwart]\ncmd = ["/usr/bin/stalwart-mail"]\n'


def test_app_processes_appended(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _make_app(bench, "mail", _GOOD)

    names = [d.name for d in _builder(bench).prod_process_definitions()]
    assert "mail-stalwart" in names
    assert "web" in names  # core processes still present


def test_app_process_is_not_critical(tmp_path: Path) -> None:
    """An app-declared process crashing must not take down the whole bench under
    the dev runner - only the bench's own core processes are critical. Confirmed
    live: without this, killing meet's sfu-server process stopped redis and every
    other bench process along with it."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    _make_app(bench, "mail", _GOOD)

    defs = {d.name: d for d in _builder(bench).prod_process_definitions()}
    assert defs["mail-stalwart"].critical is False
    assert defs["web"].critical is True  # unchanged for the bench's own processes


def test_one_bad_app_fails_loudly(tmp_path: Path) -> None:
    """A skipped app would look "removed" to systemd/supervisor, which then stop
    its running services. Refusing to build the set leaves the bench alone."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    _make_app(bench, "broken", "[tool.pilot\n")  # malformed TOML
    _make_app(bench, "mail", _GOOD)

    with pytest.raises(BenchError):
        _builder(bench).prod_process_definitions()


def test_removed_app_drops_out_of_definitions(tmp_path: Path) -> None:
    """Removing an app removes its process from the desired set, so systemd's
    existing reaping unlinks its stale unit."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    _make_app(bench, "mail", _GOOD)
    assert "mail-stalwart" in [d.name for d in _builder(bench).prod_process_definitions()]

    subprocess.run(["rm", "-rf", str(bench.apps_path / "mail")], check=True)
    assert "mail-stalwart" not in [d.name for d in _builder(bench).prod_process_definitions()]


def test_name_collision_after_dash_normalization_is_rejected(tmp_path: Path) -> None:
    """Supervisor normalizes underscores to dashes in program names, so an app
    named 'redis' declaring a process 'cache' collides with the bench's own
    redis_cache process once both are rendered - must fail loud, not silently
    duplicate the config."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    _make_app(bench, "redis", '[tool.pilot.background_processes.cache]\ncmd = ["/usr/bin/x"]\n')

    with pytest.raises(ValueError):
        _builder(bench).prod_process_definitions()


def test_disabled_app_processes_opts_out_a_broken_app(tmp_path: Path) -> None:
    """An operator can disable a specific app's declarations via bench.toml so a
    third-party app's bad declaration doesn't refuse to start the whole bench."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    _make_app(bench, "broken", "[tool.pilot\n")  # malformed TOML
    _make_app(bench, "mail", _GOOD)
    bench.config.disabled_app_processes = ["broken"]

    names = [d.name for d in _builder(bench).prod_process_definitions()]
    assert "mail-stalwart" in names
    assert "web" in names
