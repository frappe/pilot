from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pilot.managers import systemd_user
from pilot.managers.processes.definitions import ProcessDefinition
from pilot.managers.processes.systemd_render import SystemdRenderer, sandbox_properties


def _web() -> ProcessDefinition:
    return ProcessDefinition(name="web", argv=["/bin/gunicorn"], log_file=Path("/tmp/web.log"))


def test_unit_is_unsandboxed_by_default() -> None:
    unit = SystemdRenderer("bench").render(_web())

    assert "NoNewPrivileges" not in unit
    assert "ProtectSystem" not in unit


def test_sandboxed_unit_may_write_only_its_bench() -> None:
    unit = SystemdRenderer("bench", Path("/home/frappe/pilot/benches/main")).render(_web())

    assert "NoNewPrivileges=yes\n" in unit
    assert "ProtectSystem=strict\n" in unit
    assert "ProtectHome=read-only\n" in unit
    assert "ReadWritePaths=/home/frappe/pilot/benches/main\n" in unit
    assert "SystemCallFilter=@system-service\n" in unit


def test_sandbox_quotes_a_path_with_a_space() -> None:
    assert 'ReadWritePaths="/srv/my bench"' in sandbox_properties(Path("/srv/my bench"))


def test_probe_passes_every_property_to_systemd_run(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **_: calls.append(argv) or subprocess.CompletedProcess(argv, 0)
    )

    assert systemd_user.can_sandbox(["NoNewPrivileges=yes", "PrivateUsers=yes"])
    assert calls[0][-5:] == ["-p", "NoNewPrivileges=yes", "-p", "PrivateUsers=yes", "true"]


def test_probe_fails_when_systemd_run_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: subprocess.CompletedProcess(argv, 1))

    assert not systemd_user.can_sandbox(["PrivateUsers=yes"])
