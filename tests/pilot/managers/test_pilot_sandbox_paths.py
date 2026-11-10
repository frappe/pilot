from __future__ import annotations

from pathlib import Path

import pytest

from pilot.managers.processes import sandbox
from pilot.managers.processes.sandbox import WAITER, PilotSandboxPaths
from tests.pilot.commands.test_commands import make_bench


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PilotSandboxPaths:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("NVM_DIR", raising=False)
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
    monkeypatch.setattr(sandbox, "cli_root", lambda: Path("/opt/pilot"))
    monkeypatch.setattr(PilotSandboxPaths, "mariadb_socket_path", "/run/mysqld/mysqld.sock")
    return PilotSandboxPaths(make_bench(tmp_path / "benches" / "b"))


def test_only_paths_the_sandbox_masks_are_listed(paths: PilotSandboxPaths) -> None:
    # The interpreter, node and waiter here are outside the home. The socket is under /run, which the sandbox masks too.
    assert paths.read == [str(Path.home() / ".nvm"), "/run/mysqld"]


def test_a_uv_managed_interpreter_is_bound_by_the_directory_the_venv_links_to(
    paths: PilotSandboxPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    python_home = Path.home() / ".local/share/uv/python/cpython-3.14-linux"
    (python_home / "bin").mkdir(parents=True)
    real = python_home / "bin" / "python3.14"
    real.touch()
    paths.bench.env_path.joinpath("bin").mkdir(parents=True)
    paths.bench.python.symlink_to(real)

    assert str(python_home) in paths.read
    assert str(Path.home() / ".local/share/uv/python") not in paths.read


def test_node_installed_under_the_home_is_bound_by_its_prefix(
    paths: PilotSandboxPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = Path.home() / ".local/share/fnm/node-versions/v24/installation"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "node").touch()
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: str(prefix / "bin" / "node"))

    assert str(prefix) in paths.read


def test_nvm_dir_overrides_the_default(paths: PilotSandboxPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVM_DIR", str(Path.home() / "tools/nvm"))

    assert str(Path.home() / "tools/nvm") in paths.read
    assert str(Path.home() / ".nvm") not in paths.read


def test_the_database_socket_directory_and_the_waiter_are_bound_when_in_home(
    paths: PilotSandboxPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox, "cli_root", lambda: Path.home() / "pilot")
    monkeypatch.setattr(PilotSandboxPaths, "mariadb_socket_path", str(Path.home() / "pilot/databases/mariadb/run/mysqld.sock"))

    assert str(Path.home() / "pilot/databases/mariadb/run") in paths.read
    assert str(Path.home() / "pilot" / WAITER) in paths.read


def test_the_pilot_install_is_not_bound_as_a_whole(paths: PilotSandboxPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "cli_root", lambda: Path.home() / "pilot")

    assert str(Path.home() / "pilot") not in paths.read
    assert not any("databases/mariadb/data" in path for path in paths.read)


def test_the_masked_roots_are_the_home_directory_and_the_roots_the_sandbox_empties(
    paths: PilotSandboxPaths,
) -> None:
    assert Path.home() in paths.masked_roots
    assert Path("/run") in paths.masked_roots
    assert paths.is_masked(Path("/run/mysqld/mysqld.sock"))
    assert not paths.is_masked(Path("/opt/models"))
