"""Tests for capping build memory and serializing builds host-wide."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pilot.exceptions import BenchError
from pilot.managers import systemd_user
from pilot.managers.systemd_user import memory_capped, systemctl_env


def test_capped_argv_carries_the_limit(monkeypatch):
    monkeypatch.setattr(systemd_user, "has_user_memory_control", lambda: True)
    argv = memory_capped(["yarn", "build"], 400)
    assert argv[:3] == ["systemd-run", "--user", "--scope"]
    assert "MemoryMax=400M" in argv
    assert "MemorySwapMax=0" in argv
    assert argv[-2:] == ["yarn", "build"]


def test_argv_is_untouched_where_scopes_cannot_cap(monkeypatch):
    monkeypatch.setattr(systemd_user, "has_user_memory_control", lambda: False)
    assert memory_capped(["yarn", "build"], 400) == ["yarn", "build"]


def test_systemctl_env_carries_the_bus_address(monkeypatch):
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    env = systemctl_env()
    runtime_dir = f"/run/user/{os.getuid()}"
    assert env["XDG_RUNTIME_DIR"] == runtime_dir
    # Without this systemd-run starts an uncapped scope and reports success.
    assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={runtime_dir}/bus"


def test_a_killed_build_reports_memory_not_a_signal(monkeypatch):
    from pilot.exceptions import CommandError
    from pilot.managers import python_assets

    monkeypatch.setattr(systemd_user, "has_user_memory_control", lambda: True)

    def killed(argv, **kwargs):
        raise CommandError("Command 'systemd-run' failed with exit code -15.", returncode=-15)

    monkeypatch.setattr(python_assets, "run_command", killed)
    builder = python_assets.PythonAssetBuilder.__new__(python_assets.PythonAssetBuilder)
    builder._memory_max_mb = 250
    with pytest.raises(BenchError, match="ran out of memory"):
        builder.run_capped(["yarn", "build"])


def test_a_real_compiler_error_is_left_alone(monkeypatch):
    from pilot.exceptions import CommandError
    from pilot.managers import python_assets

    monkeypatch.setattr(systemd_user, "has_user_memory_control", lambda: True)

    def failed(argv, **kwargs):
        raise CommandError("syntax error in app.js", returncode=1)

    monkeypatch.setattr(python_assets, "run_command", failed)
    builder = python_assets.PythonAssetBuilder.__new__(python_assets.PythonAssetBuilder)
    builder._memory_max_mb = 250
    with pytest.raises(CommandError, match="syntax error"):
        builder.run_capped(["yarn", "build"])


def test_second_build_is_refused_while_one_runs(tmp_path, monkeypatch):
    from pilot.core.server import Server

    monkeypatch.setattr(type(Server()), "benches_dir", property(lambda self: Path(tmp_path)))
    with Server().build_action_lock():
        with pytest.raises(BenchError, match="Another build is already running"):
            with Server().build_action_lock():
                pass


def test_the_lock_is_released_after_a_build(tmp_path, monkeypatch):
    from pilot.core.server import Server

    monkeypatch.setattr(type(Server()), "benches_dir", property(lambda self: Path(tmp_path)))
    with Server().build_action_lock():
        pass
    with Server().build_action_lock():
        pass
