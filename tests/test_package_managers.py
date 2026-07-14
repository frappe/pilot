"""Tests for distro detection and system package managers."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot import package_managers, platform
from pilot.package_managers import (
    ApkPackageManager,
    AptPackageManager,
    BrewPackageManager,
    DnfPackageManager,
    PacmanPackageManager,
    get_package_manager,
)
from pilot.platform import Distro


def _write_os_release(tmp_path: Path, monkeypatch, content: str) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text(content)
    monkeypatch.setattr(platform, "_OS_RELEASE", os_release)


def _force_linux(monkeypatch) -> None:
    monkeypatch.setattr(platform, "detect", lambda: platform.Platform.LINUX)


# ── detect_distro ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("distro_id", ["debian", "ubuntu", "fedora", "arch", "alpine"])
def test_detect_distro_by_id(tmp_path: Path, monkeypatch, distro_id: str) -> None:
    _force_linux(monkeypatch)
    _write_os_release(tmp_path, monkeypatch, f'ID={distro_id}\n')
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
    _write_os_release(tmp_path, monkeypatch, 'ID=endeavouros\nID_LIKE=arch\n')
    assert platform.detect_distro() == Distro.ARCH


def test_detect_distro_unknown(tmp_path: Path, monkeypatch) -> None:
    _force_linux(monkeypatch)
    _write_os_release(tmp_path, monkeypatch, 'ID=slackware\n')
    assert platform.detect_distro() == Distro.UNKNOWN


def test_detect_distro_missing_file(tmp_path: Path, monkeypatch) -> None:
    _force_linux(monkeypatch)
    monkeypatch.setattr(platform, "_OS_RELEASE", tmp_path / "missing")
    assert platform.detect_distro() == Distro.UNKNOWN


def test_detect_distro_not_linux(monkeypatch) -> None:
    monkeypatch.setattr(platform, "detect", lambda: platform.Platform.MACOS)
    assert platform.detect_distro() == Distro.UNKNOWN


def test_is_alpine_from_os_release(tmp_path: Path, monkeypatch) -> None:
    _force_linux(monkeypatch)
    _write_os_release(tmp_path, monkeypatch, 'ID=alpine\n')
    assert platform.detect_distro() == Distro.ALPINE
    assert platform.is_alpine() is True


# ── get_package_manager dispatch ──────────────────────────────────────────────

@pytest.mark.parametrize(
    ("distro", "manager_class"),
    [
        (Distro.DEBIAN, AptPackageManager),
        (Distro.UBUNTU, AptPackageManager),
        (Distro.UNKNOWN, AptPackageManager),
        (Distro.FEDORA, DnfPackageManager),
        (Distro.ARCH, PacmanPackageManager),
        (Distro.ALPINE, ApkPackageManager),
    ],
)
def test_get_package_manager_dispatch(monkeypatch, distro, manager_class) -> None:
    monkeypatch.setattr(package_managers, "is_macos", lambda: False)
    monkeypatch.setattr(package_managers, "detect_distro", lambda: distro)
    assert isinstance(get_package_manager(), manager_class)


def test_get_package_manager_macos(monkeypatch) -> None:
    monkeypatch.setattr(package_managers, "is_macos", lambda: True)
    assert isinstance(get_package_manager(), BrewPackageManager)


# ── alias resolution ──────────────────────────────────────────────────────────

def test_resolve_passes_unmapped_names_through() -> None:
    assert DnfPackageManager()._resolve("nginx", "certbot") == ["nginx", "certbot"]


def test_resolve_expands_tuple_aliases() -> None:
    resolved = DnfPackageManager()._resolve("build-essential")
    assert resolved == ["gcc", "gcc-c++", "make"]


def test_resolve_deduplicates() -> None:
    # Alpine aliases mariadb-server onto the same package as a bare mariadb.
    assert ApkPackageManager()._resolve("mariadb-server", "mariadb") == ["mariadb"]


# ── install/is_installed/update argv ──────────────────────────────────────────

def _run_as_root(monkeypatch) -> None:
    monkeypatch.setattr(platform, "is_root", lambda: True)


@pytest.mark.parametrize(
    ("manager", "expected"),
    [
        (DnfPackageManager(), ["dnf", "install", "-y", "nginx"]),
        (PacmanPackageManager(), ["pacman", "-S", "--noconfirm", "--needed", "nginx"]),
        (ApkPackageManager(), ["apk", "add", "--no-cache", "nginx"]),
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
        (ApkPackageManager(), ["apk", "info", "-e", "redis"]),
        (AptPackageManager(), ["dpkg", "-l", "redis-server"]),
    ],
)
def test_is_installed_argv(monkeypatch, manager, expected) -> None:
    with patch.object(package_managers.subprocess, "run") as run:
        run.return_value.returncode = 0
        assert manager.is_installed("redis-server") is True
    assert run.call_args[0][0] == expected


@pytest.mark.parametrize(
    ("manager", "expected"),
    [
        (DnfPackageManager(), ["dnf", "-y", "makecache"]),
        (PacmanPackageManager(), ["pacman", "-Sy", "--noconfirm"]),
        (ApkPackageManager(), ["apk", "update"]),
        (AptPackageManager(), ["apt-get", "-y", "update"]),
    ],
)
def test_update_argv(monkeypatch, manager, expected) -> None:
    _run_as_root(monkeypatch)
    with patch.object(package_managers.subprocess, "run") as run:
        manager.update()
    assert run.call_args[0][0] == expected


def test_install_uses_sudo_when_not_root(monkeypatch) -> None:
    monkeypatch.setattr(platform, "is_root", lambda: False)
    with patch.object(package_managers.subprocess, "run") as run:
        DnfPackageManager().install("git")
    assert run.call_args[0][0] == ["sudo", "dnf", "install", "-y", "git"]


# ── node install branching ────────────────────────────────────────────────────

def _node_install_commands(monkeypatch, distro: Distro) -> list[list[str]]:
    from pilot.managers import python_env_manager as module

    manager = module.PythonEnvManager(bench=None)
    commands: list[list[str]] = []
    monkeypatch.setattr(module, "is_macos", lambda: False)
    monkeypatch.setattr(module, "detect_distro", lambda: distro)
    monkeypatch.setattr(module, "run_command", lambda argv, **kwargs: commands.append(argv))
    with patch.object(module, "get_package_manager") as gpm:
        manager._install_node()
        if gpm.return_value.install.called:
            commands.append(list(gpm.return_value.install.call_args[0]))
    return commands


def test_install_node_debian_uses_nodesource_deb(monkeypatch) -> None:
    commands = _node_install_commands(monkeypatch, Distro.DEBIAN)
    assert "deb.nodesource.com" in commands[0][2]
    assert commands[1] == ["sudo", "apt-get", "install", "-y", "nodejs"]


def test_install_node_fedora_uses_nodesource_rpm(monkeypatch) -> None:
    commands = _node_install_commands(monkeypatch, Distro.FEDORA)
    assert "rpm.nodesource.com" in commands[0][2]
    assert commands[1] == ["sudo", "dnf", "install", "-y", "nodejs"]


@pytest.mark.parametrize("distro", [Distro.ARCH, Distro.ALPINE])
def test_install_node_native_repos(monkeypatch, distro) -> None:
    commands = _node_install_commands(monkeypatch, distro)
    assert commands == [["nodejs", "npm"]]
