"""Tests for capping the memory an asset build may use."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from pilot.config.build import BuildConfig
from pilot.core import build_memory
from pilot.core.build_memory import (
    BUILD_MEMORY_SHARE,
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
    _free_memory(monkeypatch, 256)
    assert build_memory_limit_mb(2048) == 2048


def test_a_low_memory_host_still_gets_a_capped_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """The kernel kills the scope if the build outgrows the cap; swap may let it finish."""
    _free_memory(monkeypatch, 256)
    assert build_memory_limit_mb() == int(256 * BUILD_MEMORY_SHARE)


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
    monkeypatch.setattr(build_memory, "free_swap_mb", lambda: 2048)
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
    # Small hosts finish large frontend builds only by swapping past the cap, but not into all of it.
    assert f"MemorySwapMax={int(2048 * BUILD_MEMORY_SHARE)}M" in calls[0]


def test_free_swap_is_read_from_meminfo_and_absent_swap_is_none(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable:    2097152 kB\nSwapFree:        1048576 kB\n")
    assert build_memory.free_swap_mb(meminfo) == 1024

    meminfo.write_text("MemAvailable:    2097152 kB\n")
    assert build_memory.free_swap_mb(meminfo) == 0


def test_a_linux_host_without_memory_control_builds_uncapped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _host(monkeypatch, reads_memory=True, caps_memory=False)

    _builder().run_compiler(["yarn", "build"])

    assert calls == [["yarn", "build"]]


def test_a_low_memory_host_without_memory_control_builds_uncapped(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = _host(monkeypatch, reads_memory=True, caps_memory=False, free_mb=256)

    _builder().run_compiler(["yarn", "build"])

    assert calls == [["yarn", "build"]]
    assert "runs uncapped" in caplog.text


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


def _build_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> dict:
    environments: list[dict] = []
    monkeypatch.setattr(build_memory, "can_read_memory", lambda: True)
    monkeypatch.setattr(python_assets, "can_cap_memory", lambda: True)
    _free_memory(monkeypatch, 8192)
    monkeypatch.setattr(python_assets, "run_command", lambda argv, **kwargs: environments.append(kwargs["env"]))
    _builder().run_compiler(["yarn", "build"], env=env)
    return environments[0]


def test_node_heap_follows_the_build_memory_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Node's default heap fails large frontends long before the cap."""
    env = _build_env(monkeypatch, NODE_OPTIONS="--enable-source-maps")

    heap_mb = int(8192 * BUILD_MEMORY_SHARE) * 3 // 4
    assert env["NODE_OPTIONS"] == f"--enable-source-maps --max-old-space-size={heap_mb}"


def test_an_operator_node_heap_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _build_env(monkeypatch, NODE_OPTIONS="--max-old-space-size=3000")

    assert env["NODE_OPTIONS"] == "--max-old-space-size=3000"


def test_a_capped_compiler_error_names_the_compiler(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed(argv, **kwargs):
        raise CommandError("Command 'systemd-run' failed with exit code 1.", returncode=1)

    _host(monkeypatch, reads_memory=True, caps_memory=True)
    monkeypatch.setattr(python_assets, "run_command", failed)
    with pytest.raises(CommandError, match="Command 'yarn' failed with exit code 1"):
        _builder().run_compiler(["yarn", "build"])


@pytest.mark.parametrize("trial_exit_code, expected", [(0, True), (1, False)])
def test_memory_is_capped_only_where_a_trial_scope_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, trial_exit_code: int, expected: bool
) -> None:
    """Without a user bus, systemd-run exits 1 before the build starts, so every build would fail."""
    import subprocess
    from types import SimpleNamespace

    from pilot.managers import systemd_user

    controllers = tmp_path / "cgroup.controllers"
    controllers.write_text("cpu memory pids")
    monkeypatch.setattr(systemd_user, "Path", lambda _path: controllers)
    monkeypatch.setattr(systemd_user.shutil, "which", lambda _name: "/usr/bin/systemd-run")
    trials: list[list[str]] = []

    def trial(argv, **kwargs):
        trials.append(argv)
        return SimpleNamespace(returncode=trial_exit_code)

    monkeypatch.setattr(subprocess, "run", trial)

    assert systemd_user.can_cap_memory() is expected
    assert trials[0][0] == "systemd-run"
    assert trials[0][-1] == "true"


@pytest.mark.parametrize("error", [OSError("sudo missing"), subprocess.TimeoutExpired("sudo", 120)])
def test_a_failing_swap_helper_only_warns(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    """Swap is optional, so the build must not fail when the helper cannot run."""

    def raise_error(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(build_memory.subprocess, "run", raise_error)

    assert build_memory._has_switched_build_swap("on") is False
