"""Tests for capping the memory an asset build may use."""

from __future__ import annotations

import os

import psutil

from pilot.core.build_memory import BUILD_MEMORY_SHARE, build_memory_limit_mb
from pilot.managers.systemd_user import systemctl_env


def test_the_limit_is_a_share_of_free_memory():
    available_mb = psutil.virtual_memory().available / (1024 * 1024)
    assert build_memory_limit_mb() == int(available_mb * BUILD_MEMORY_SHARE)


def test_the_limit_never_exceeds_what_is_free():
    """A limit above free memory is one the host cannot honour."""
    assert build_memory_limit_mb() <= psutil.virtual_memory().available / (1024 * 1024)


def test_systemctl_env_carries_the_bus_address(monkeypatch):
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    env = systemctl_env()
    runtime_dir = f"/run/user/{os.getuid()}"
    # Without this systemd-run starts an uncapped scope and reports success.
    assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={runtime_dir}/bus"


def test_a_killed_build_reports_memory_not_a_signal(monkeypatch):
    import pytest

    from pilot.exceptions import BenchError, CommandError
    from pilot.managers import python_assets

    def killed(argv, **kwargs):
        raise CommandError("Command 'systemd-run' failed with exit code -9.", returncode=-9)

    monkeypatch.setattr(python_assets, "run_command", killed)
    builder = python_assets.PythonAssetBuilder.__new__(python_assets.PythonAssetBuilder)
    with pytest.raises(BenchError, match="ran out of memory"):
        builder.run_compiler(["yarn", "build"])


def test_a_compiler_error_is_left_alone(monkeypatch):
    import pytest

    from pilot.exceptions import CommandError
    from pilot.managers import python_assets

    def failed(argv, **kwargs):
        raise CommandError("syntax error in app.js", returncode=1)

    monkeypatch.setattr(python_assets, "run_command", failed)
    builder = python_assets.PythonAssetBuilder.__new__(python_assets.PythonAssetBuilder)
    with pytest.raises(CommandError, match="syntax error"):
        builder.run_compiler(["yarn", "build"])
