"""Tests for pilot.managers.platform helpers."""
from __future__ import annotations

import threading
from pathlib import Path

from pilot.managers import platform


def test_which_searches_sbin_when_path_is_minimal(tmp_path: Path, monkeypatch) -> None:
    sbin = tmp_path / "usr" / "sbin"
    sbin.mkdir(parents=True)
    daemon = sbin / "mariadbd"
    daemon.write_text("#!/bin/sh\n")
    daemon.chmod(0o755)

    # Minimal PATH without the sbin dir — shutil.which alone would miss it.
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setattr(platform, "_EXTRA_BIN_DIRS", (str(sbin),))

    assert platform.which("mariadbd") == str(daemon)


def test_which_returns_none_for_missing(monkeypatch) -> None:
    monkeypatch.setattr(platform, "_EXTRA_BIN_DIRS", ())
    assert platform.which("definitely-not-a-real-binary-xyz") is None


def test_noninteractive_privileges_are_isolated_to_the_current_thread(monkeypatch) -> None:
    monkeypatch.setattr(platform, "is_root", lambda: False)
    entered = threading.Event()
    release = threading.Event()
    command_from_thread: list[str] = []

    def build_noninteractive_command() -> None:
        with platform.noninteractive_privileges():
            entered.set()
            assert release.wait(timeout=1)
            command_from_thread.extend(platform._privileged(["true"]))

    thread = threading.Thread(target=build_noninteractive_command)
    thread.start()
    assert entered.wait(timeout=1)
    assert platform._privileged(["true"]) == ["sudo", "true"]
    release.set()
    thread.join(timeout=1)

    assert command_from_thread == ["sudo", "-n", "true"]


def test_task_environment_forces_noninteractive_privileges(monkeypatch) -> None:
    monkeypatch.setattr(platform, "is_root", lambda: False)
    monkeypatch.setenv(platform.NONINTERACTIVE_PRIVILEGES_ENV, "1")

    assert platform._privileged(["true"]) == ["sudo", "-n", "true"]
