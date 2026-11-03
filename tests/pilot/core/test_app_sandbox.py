from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pilot.core.app.sandbox import AppSandbox, SandboxPaths


def _error(message: str) -> Exception:
    return ValueError(message)


def _paths(**declaration) -> SandboxPaths:
    return SandboxPaths.from_declaration(declaration, "test", _error)


def test_tilde_paths_expand_to_the_home_directory() -> None:
    paths = _paths(read_paths=["~/.cache/model"], write_paths=["/srv/data"])

    assert paths.read == (str(Path.home() / ".cache/model"),)
    assert paths.write == ("/srv/data",)


def test_nothing_declared_is_empty() -> None:
    assert _paths() == SandboxPaths()


@pytest.mark.parametrize(
    "declaration",
    [
        {"read_paths": "~/x"},
        {"read_paths": [1]},
        {"write_paths": ["relative/dir"]},
        {"read_paths": ["~/a/../.ssh"]},
        {"read_paths": ["~/bad\npath"]},
    ],
)
def test_malformed_paths_are_rejected(declaration: dict) -> None:
    with pytest.raises(ValueError):
        _paths(**declaration)


@pytest.mark.parametrize(
    "path",
    ["~/.ssh", "~/.ssh/id_ed25519", "~/.gnupg", "~/.config/systemd/user", "~/.aws", "~/.kube", "~", "~/.config"],
)
def test_credential_paths_and_their_parents_are_refused(path: str) -> None:
    home = str(Path.home())
    resolved = home if path == "~" else path
    with pytest.raises(ValueError, match="never exposes"):
        _paths(write_paths=[resolved.replace("~", home, 1)] if path != "~" else [home])


def test_the_home_directory_itself_is_refused() -> None:
    with pytest.raises(ValueError, match="never exposes"):
        _paths(read_paths=[str(Path.home())])


def _app(tmp_path: Path, pyproject: str | None) -> SimpleNamespace:
    if pyproject is not None:
        (tmp_path / "pyproject.toml").write_text(pyproject)
    return SimpleNamespace(path=tmp_path)


def test_the_sandbox_table_is_read_from_pyproject(tmp_path: Path) -> None:
    app = _app(tmp_path, '[tool.pilot.sandbox]\nread_paths = ["/opt/models"]\nwrite_paths = ["/var/cache/app"]\n')

    paths = AppSandbox(app).get_paths(_error)

    assert paths == SandboxPaths(read=("/opt/models",), write=("/var/cache/app",))


def test_no_pyproject_or_no_table_means_no_paths(tmp_path: Path) -> None:
    assert AppSandbox(_app(tmp_path, None)).get_paths(_error) == SandboxPaths()
    assert AppSandbox(_app(tmp_path, "[tool.other]\nx = 1\n")).get_paths(_error) == SandboxPaths()


def test_a_non_table_sandbox_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be a table"):
        AppSandbox(_app(tmp_path, "[tool.pilot]\nsandbox = 1\n")).get_paths(_error)
