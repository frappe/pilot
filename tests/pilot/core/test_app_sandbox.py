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


MESSAGE = "must be a directory below one of"


@pytest.mark.parametrize(
    "path",
    [
        "~/.ssh",
        "~/.ssh/id_ed25519",
        "~/.gnupg",
        "~/.aws",
        "~/.kube",
        "~/.config",
        "~/.config/systemd/user",
        "~/.config/autostart",
        "~/.config/gh",
        "~/.bashrc",
        "~/.profile",
        "~/.zshrc",
        "~/.gitconfig",
        "~/.npmrc",
        "~/.netrc",
        "~/.docker",
        "~/.mozilla",
        "~/.bash_history",
        "~/.local/bin",
        "~/.password-store",
    ],
)
def test_paths_outside_the_allowed_roots_are_refused(path: str) -> None:
    with pytest.raises(ValueError, match=MESSAGE):
        _paths(write_paths=[path])
    with pytest.raises(ValueError, match=MESSAGE):
        _paths(read_paths=[path])


@pytest.mark.parametrize("path", ["~", str(Path.home()), "/home"])
def test_the_home_directory_is_refused(path: str) -> None:
    with pytest.raises(ValueError):
        _paths(read_paths=[path])


@pytest.mark.parametrize("path", ["~/.cache", "~/.local/share", "~/.local/state", "/opt", "/srv", "/var/lib"])
def test_an_allowed_root_itself_is_refused(path: str) -> None:
    with pytest.raises(ValueError, match=MESSAGE):
        _paths(write_paths=[path])


@pytest.mark.parametrize(
    "path",
    [
        "~/.local/share/keyrings",
        "~/.local/share/applications/evil.desktop",
        "~/.local/share/systemd/user",
        "~/.local/share/uv/python",
        "~/.local/share/pilot",
        "~/.cache/autostart",
        "/var/lib/systemd",
    ],
)
def test_reserved_directories_inside_an_allowed_root_are_refused(path: str) -> None:
    with pytest.raises(ValueError, match=MESSAGE):
        _paths(write_paths=[path])


@pytest.mark.parametrize(
    "path",
    [
        "~/.cache/model",
        "~/.local/share/mail/data",
        "~/.local/state/mail",
        "/opt/models",
        "/srv/data",
        "/var/lib/stalwart",
        "/var/cache/mail",
        "/usr/local/share/fonts",
    ],
)
def test_a_directory_below_an_allowed_root_is_kept(path: str) -> None:
    assert _paths(write_paths=[path]).write


@pytest.mark.parametrize("path", ["/etc", "/proc/1", "/sys", "/dev", "/run/user/1000", "/home", "/", "/tmp"])
def test_system_paths_are_refused(path: str) -> None:
    with pytest.raises(ValueError, match=MESSAGE):
        _paths(read_paths=[path])


def test_a_link_inside_an_allowed_root_cannot_lead_out_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "innocent").symlink_to(tmp_path / ".ssh")

    with pytest.raises(ValueError, match=MESSAGE):
        _paths(read_paths=["~/.cache/innocent"])


def test_a_link_that_stays_inside_an_allowed_root_is_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cache" / "real").mkdir(parents=True)
    (tmp_path / ".cache" / "alias").symlink_to(tmp_path / ".cache" / "real")

    assert _paths(read_paths=["~/.cache/alias"]).read == (str((tmp_path / ".cache" / "real").resolve()),)


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
