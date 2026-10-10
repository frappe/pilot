"""Tests for distro detection and system package managers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.managers import packages as package_managers
from pilot.managers import platform
from pilot.managers.packages import (
    AptPackageManager,
    BrewPackageManager,
    DnfPackageManager,
    PacmanPackageManager,
    get_package_manager,
)
from pilot.managers.platform import Distro


def _write_os_release(tmp_path: Path, monkeypatch, content: str) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text(content)
    monkeypatch.setattr(platform, "_OS_RELEASE", os_release)


def _force_linux(monkeypatch) -> None:
    monkeypatch.setattr(platform, "detect", lambda: platform.Platform.LINUX)


@pytest.mark.parametrize("distro_id", ["debian", "ubuntu", "fedora", "arch"])
def test_detect_distro_by_id(tmp_path: Path, monkeypatch, distro_id: str) -> None:
    _force_linux(monkeypatch)
    _write_os_release(tmp_path, monkeypatch, f"ID={distro_id}\n")
    assert platform.detect_distro() == Distro(distro_id)


def test_detect_distro_strips_quotes(tmp_path: Path, monkeypatch) -> None:
    _force_linux(monkeypatch)
    _write_os_release(tmp_path, monkeypatch, 'ID="fedora"\nPRETTY_NAME="Fedora Linux 42"\n')
    assert platform.detect_distro() == Distro.FEDORA


def test_detect_distro_id_like_fallback(tmp_path: Path, monkeypatch) -> None:
    _force_linux(monkeypatch)
    _write_os_release(tmp_path, monkeypatch, 'ID=linuxmint\nID_LIKE="ubuntu debian"\n')
    assert platform.detect_distro() == Distro.UBUNTU


def test_detect_distro_id_like_arch_derivative(tmp_path: Path, monkeypatch) -> None:
    _force_linux(monkeypatch)
    _write_os_release(tmp_path, monkeypatch, "ID=endeavouros\nID_LIKE=arch\n")
    assert platform.detect_distro() == Distro.ARCH


def test_detect_distro_unknown(tmp_path: Path, monkeypatch) -> None:
    _force_linux(monkeypatch)
    _write_os_release(tmp_path, monkeypatch, "ID=slackware\n")
    assert platform.detect_distro() == Distro.UNKNOWN


def test_detect_distro_missing_file(tmp_path: Path, monkeypatch) -> None:
    _force_linux(monkeypatch)
    monkeypatch.setattr(platform, "_OS_RELEASE", tmp_path / "missing")
    assert platform.detect_distro() == Distro.UNKNOWN


def test_detect_distro_not_linux(monkeypatch) -> None:
    monkeypatch.setattr(platform, "detect", lambda: platform.Platform.MACOS)
    assert platform.detect_distro() == Distro.UNKNOWN


@pytest.mark.parametrize(
    ("distro", "manager_class"),
    [
        (Distro.DEBIAN, AptPackageManager),
        (Distro.UBUNTU, AptPackageManager),
        (Distro.UNKNOWN, AptPackageManager),
        (Distro.FEDORA, DnfPackageManager),
        (Distro.ARCH, PacmanPackageManager),
    ],
)
def test_get_package_manager_dispatch(monkeypatch, distro, manager_class) -> None:
    monkeypatch.setattr(package_managers, "is_macos", lambda: False)
    monkeypatch.setattr(package_managers, "detect_distro", lambda: distro)
    assert isinstance(get_package_manager(), manager_class)


def test_get_package_manager_macos(monkeypatch) -> None:
    monkeypatch.setattr(package_managers, "is_macos", lambda: True)
    assert isinstance(get_package_manager(), BrewPackageManager)


def test_resolve_passes_unmapped_names_through() -> None:
    assert DnfPackageManager()._resolve("nginx", "certbot") == ["nginx", "certbot"]


def test_resolve_expands_tuple_aliases() -> None:
    resolved = DnfPackageManager()._resolve("build-essential")
    assert resolved == ["gcc", "gcc-c++", "make"]


def test_resolve_deduplicates() -> None:
    # Pacman aliases mariadb-server onto the same package as a bare mariadb.
    assert PacmanPackageManager()._resolve("mariadb-server", "mariadb") == ["mariadb"]


def _run_as_root(monkeypatch) -> None:
    monkeypatch.setattr(platform, "is_root", lambda: True)


@pytest.mark.parametrize(
    ("manager", "expected"),
    [
        (DnfPackageManager(), ["dnf", "install", "-y", "nginx"]),
        (PacmanPackageManager(), ["pacman", "-S", "--noconfirm", "--needed", "nginx"]),
        (AptPackageManager(), ["apt-get", "install", "-y", "nginx"]),
    ],
)
def test_install_argv(monkeypatch, manager, expected) -> None:
    _run_as_root(monkeypatch)
    with patch.object(package_managers.subprocess, "run") as run:
        manager.install("nginx")
    assert run.call_args[0][0] == expected


@pytest.mark.parametrize(
    ("manager", "expected"),
    [
        (DnfPackageManager(), ["rpm", "-q", "valkey"]),
        (PacmanPackageManager(), ["pacman", "-Qi", "valkey"]),
        (AptPackageManager(), ["dpkg", "-l", "redis-server"]),
    ],
)
def test_is_installed_argv(monkeypatch, manager, expected) -> None:
    with patch.object(package_managers.subprocess, "run") as run:
        run.return_value.returncode = 0
        assert manager.is_installed("redis-server") is True
    assert run.call_args[0][0] == expected


def test_install_uses_sudo_when_not_root(monkeypatch) -> None:
    monkeypatch.setattr(platform, "is_root", lambda: False)
    monkeypatch.setattr(package_managers, "has_passwordless_sudo", lambda: True)
    with patch.object(package_managers.subprocess, "run") as run:
        DnfPackageManager().install("git")
    assert run.call_args[0][0] == ["sudo", "dnf", "install", "-y", "git"]


def test_install_raises_without_passwordless_sudo(monkeypatch) -> None:
    from pilot.exceptions import BenchError

    monkeypatch.setattr(package_managers, "is_root", lambda: False)
    monkeypatch.setattr(package_managers, "has_passwordless_sudo", lambda: False)
    with pytest.raises(BenchError, match="Required: git"):
        DnfPackageManager().install("git")


def test_install_skips_permission_check_on_macos(monkeypatch) -> None:
    monkeypatch.setattr(package_managers, "is_root", lambda: False)
    monkeypatch.setattr(package_managers, "has_passwordless_sudo", lambda: False)
    with patch.object(package_managers.subprocess, "run") as run:
        BrewPackageManager().install("git")
    assert run.call_args[0][0] == ["brew", "install", "git"]


# Node.js is installed once by install.sh's root bootstrap now, not per bench
# init - _install_node only covers macOS (brew); everywhere else it fails loud
# rather than shelling out to sudo.


def test_install_node_uses_brew_on_macos(monkeypatch) -> None:
    from pilot.managers import environment as module

    manager = module.PythonEnvManager(bench=None)
    commands: list[list[str]] = []
    monkeypatch.setattr(module, "is_macos", lambda: True)
    monkeypatch.setattr(module, "run_command", lambda argv, **kwargs: commands.append(argv))
    manager._install_node()
    assert commands == [["brew", "install", "node"]]


def test_install_node_raises_on_other_linux(monkeypatch) -> None:
    from pilot.exceptions import BenchError
    from pilot.managers import environment as module

    manager = module.PythonEnvManager(bench=None)
    monkeypatch.setattr(module, "is_macos", lambda: False)
    with pytest.raises(BenchError, match=r"install\.sh"):
        manager._install_node()


def test_node_dependencies_disable_io_uring_without_losing_environment(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from pilot.managers import environment as module

    (tmp_path / "package.json").write_text("{}")
    app = SimpleNamespace(path=tmp_path)
    bench = SimpleNamespace(apps=lambda: [app])
    monkeypatch.setenv("PILOT_INSTALL_TEST", "preserved")
    monkeypatch.setenv("UV_USE_IO_URING", "1")
    monkeypatch.setattr(module, "get_yarn_bin", lambda: "/usr/bin/yarn")

    with patch.object(module, "run_command") as run:
        module.PythonEnvManager(bench).install_node_dependencies()

    assert run.call_args.args == (["/usr/bin/yarn", "install", "--frozen-lockfile"],)
    assert run.call_args.kwargs["cwd"] == tmp_path
    assert run.call_args.kwargs["env"]["UV_USE_IO_URING"] == "0"
    assert run.call_args.kwargs["env"]["PILOT_INSTALL_TEST"] == "preserved"
    assert module.os.environ["UV_USE_IO_URING"] == "1"


@pytest.mark.parametrize(("version", "is_accepted"), [("v18.20.3", False), ("v24.1.0", True), ("v26.0.0", True)])
def test_node_version_is_checked_before_yarn_runs(monkeypatch, version: str, is_accepted: bool) -> None:
    from types import SimpleNamespace

    from pilot.exceptions import BenchError
    from pilot.managers import environment as module

    monkeypatch.setattr(module, "run_command", lambda argv, **kwargs: SimpleNamespace(stdout=version.encode()))
    monkeypatch.setattr(module, "which", lambda name: f"/usr/bin/{name}")

    if is_accepted:
        module.PythonEnvManager.require_node_version()
    else:
        with pytest.raises(BenchError, match=r"Node\.js 24 or later is required"):
            module.PythonEnvManager.require_node_version()
