"""Tests for capping the memory an asset build may use."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pilot.config.build import BuildConfig
from pilot.core import build_memory
from pilot.core.build_memory import (
    BUILD_MEMORY_SHARE,
    MIN_BUILD_MEMORY_MB,
    available_memory_mb,
    build_memory_limit_mb,
)
from pilot.exceptions import BenchError, CommandError
from pilot.managers import python_assets
from pilot.managers.systemd_user import systemctl_env


class _FakeBench:
    class config:
        build = BuildConfig()


def _builder() -> python_assets.PythonAssetBuilder:
    builder = python_assets.PythonAssetBuilder.__new__(python_assets.PythonAssetBuilder)
    builder.bench = _FakeBench()
    return builder


def _free_memory(monkeypatch: pytest.MonkeyPatch, megabytes: int) -> None:
    monkeypatch.setattr(build_memory, "available_memory_mb", lambda: megabytes)


def test_free_memory_is_read_from_meminfo(tmp_path: Path) -> None:
    """The CLI runs on the host's python3, so it cannot rely on psutil being there."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:        4015880 kB\nMemFree:          181176 kB\nMemAvailable:    2097152 kB\n"
    )

    assert available_memory_mb(meminfo) == 2048


def test_meminfo_without_available_memory_is_refused(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        4015880 kB\n")

    with pytest.raises(BenchError, match="MemAvailable"):
        available_memory_mb(meminfo)


def test_the_limit_is_a_share_of_free_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    _free_memory(monkeypatch, 4096)
    assert build_memory_limit_mb() == int(4096 * BUILD_MEMORY_SHARE)


def test_a_manual_override_is_used_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    _free_memory(monkeypatch, MIN_BUILD_MEMORY_MB - 1)
    assert build_memory_limit_mb(2048) == 2048


def test_a_starved_host_refuses_up_front(monkeypatch: pytest.MonkeyPatch) -> None:
    _free_memory(monkeypatch, MIN_BUILD_MEMORY_MB - 1)
    with pytest.raises(BenchError, match="Not enough free memory"):
        build_memory_limit_mb()


def test_systemctl_env_carries_the_bus_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    env = systemctl_env()
    runtime_dir = f"/run/user/{os.getuid()}"
    # Without this systemd-run starts an uncapped scope and reports success.
    assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={runtime_dir}/bus"


def _host(
    monkeypatch: pytest.MonkeyPatch, reads_memory: bool, caps_memory: bool, free_mb: int = 4096
) -> list:
    """A host that does or does not report its memory and cap a build. Returns the commands run."""
    calls: list[list[str]] = []
    monkeypatch.setattr(build_memory, "can_read_memory", lambda: reads_memory)
    monkeypatch.setattr(python_assets, "can_cap_memory", lambda: caps_memory)
    _free_memory(monkeypatch, free_mb)
    monkeypatch.setattr(python_assets, "run_command", lambda argv, **kwargs: calls.append(argv))
    return calls


def test_macos_builds_uncapped_without_reading_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS has no systemd and no /proc/meminfo."""
    calls = _host(monkeypatch, reads_memory=False, caps_memory=False)
    monkeypatch.setattr(build_memory, "available_memory_mb", lambda: pytest.fail("memory was read"))

    _builder().run_compiler(["yarn", "build"])

    assert calls == [["yarn", "build"]]


def test_a_host_that_can_cap_runs_the_build_in_a_capped_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _host(monkeypatch, reads_memory=True, caps_memory=True)

    _builder().run_compiler(["yarn", "build"])

    assert calls[0][0] == "systemd-run"
    assert f"MemoryMax={int(4096 * BUILD_MEMORY_SHARE)}M" in calls[0]


def test_a_linux_host_without_memory_control_builds_uncapped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _host(monkeypatch, reads_memory=True, caps_memory=False)

    _builder().run_compiler(["yarn", "build"])

    assert calls == [["yarn", "build"]]


def test_a_starved_host_is_refused_even_where_it_cannot_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uncapped, the build would take the memory the host's other services need."""
    calls = _host(monkeypatch, reads_memory=True, caps_memory=False, free_mb=MIN_BUILD_MEMORY_MB - 1)

    with pytest.raises(BenchError, match="Not enough free memory"):
        _builder().run_compiler(["yarn", "build"])

    assert calls == []


def test_a_killed_build_reports_memory_not_a_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    def killed(argv, **kwargs):
        raise CommandError("Command 'systemd-run' failed with exit code -9.", returncode=-9)

    _host(monkeypatch, reads_memory=True, caps_memory=True)
    monkeypatch.setattr(python_assets, "run_command", killed)
    with pytest.raises(BenchError, match="ran out of memory"):
        _builder().run_compiler(["yarn", "build"])


def test_an_uncapped_build_killed_by_a_signal_is_not_blamed_on_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def killed(argv, **kwargs):
        raise CommandError("Command 'yarn' failed with exit code -15.", returncode=-15)

    _host(monkeypatch, reads_memory=True, caps_memory=False)
    monkeypatch.setattr(python_assets, "run_command", killed)
    with pytest.raises(CommandError, match="exit code -15"):
        _builder().run_compiler(["yarn", "build"])


def test_a_compiler_error_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed(argv, **kwargs):
        raise CommandError("syntax error in app.js", returncode=1)

    _host(monkeypatch, reads_memory=False, caps_memory=False)
    monkeypatch.setattr(python_assets, "run_command", failed)
    with pytest.raises(CommandError, match="syntax error"):
        _builder().run_compiler(["yarn", "build"])
