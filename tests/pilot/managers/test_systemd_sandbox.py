from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pilot.config.production import ProductionConfig
from pilot.exceptions import BenchError
from pilot.managers import systemd_user
from pilot.managers.processes.definitions import ProcessDefinition
from pilot.managers.processes.systemd_render import SystemdRenderer, get_sandbox_properties
from tests.pilot.managers.test_managers_extra import make_bench


def _definition(name: str = "web") -> ProcessDefinition:
    return ProcessDefinition(name=name, argv=["/bin/web"], log_file=Path("/srv/b/logs/web.log"))


def test_sandboxed_workload_unit_carries_the_directives_in_service() -> None:
    unit = SystemdRenderer("b", sandbox_properties=["NoNewPrivileges=yes"]).render(_definition())

    service = unit.split("[Service]\n")[1]
    assert "NoNewPrivileges=yes\n" in service


def test_admin_unit_is_never_sandboxed() -> None:
    renderer = SystemdRenderer("b", sandbox_properties=["NoNewPrivileges=yes"])

    assert "NoNewPrivileges" not in renderer.render_admin_service(_definition("admin"), "b-admin.socket")


def test_unsandboxed_unit_has_no_directives() -> None:
    assert "NoNewPrivileges" not in SystemdRenderer("b").render(_definition())


BENCH = Path("/srv/pilot/benches/b")
BENCHES = Path("/srv/pilot/benches")

SANDBOX_DIRECTIVES = {
    "NoNewPrivileges=yes",
    "PrivateUsers=yes",
    "SystemCallFilter=@system-service",
    "SystemCallArchitectures=native",
    "SystemCallErrorNumber=EPERM",
    "ProtectKernelTunables=yes",
    "ProtectKernelModules=yes",
    "ProtectKernelLogs=yes",
    "ProtectControlGroups=yes",
    "ProtectClock=yes",
    "ProtectHostname=yes",
    "PrivateDevices=yes",
    "RestrictNamespaces=yes",
    "RestrictRealtime=yes",
    "RestrictSUIDSGID=yes",
    "LockPersonality=yes",
    "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK",
    "ProtectSystem=strict",
    "ProtectHome=read-only",
    "PrivateTmp=yes",
    "TemporaryFileSystem=/srv/pilot/benches:ro",
    "BindPaths=/srv/pilot/benches/b",
    "ReadWritePaths=/srv/pilot/benches/b",
    f"InaccessiblePaths=-{Path.home()}/.ssh -{Path.home()}/.config/systemd",
}


def test_the_sandbox_directives() -> None:
    assert set(get_sandbox_properties(BENCH, BENCHES)) == SANDBOX_DIRECTIVES


def test_a_bench_path_with_a_space_is_quoted() -> None:
    properties = get_sandbox_properties(Path("/srv/my benches/b"), Path("/srv/my benches"))

    assert 'BindPaths="/srv/my benches/b"' in properties
    assert 'TemporaryFileSystem="/srv/my benches:ro"' in properties


@pytest.mark.parametrize(
    "exit_code, stderr, expected",
    [(0, "", ""), (1, "Unknown assignment: PrivateUsers=yes\n", "Unknown assignment: PrivateUsers=yes")],
)
def test_the_probe_reports_why_a_trial_unit_failed(
    monkeypatch: pytest.MonkeyPatch, exit_code: int, stderr: str, expected: str
) -> None:
    trials: list[list[str]] = []

    def trial(argv, **_kwargs):
        trials.append(argv)
        return SimpleNamespace(returncode=exit_code, stderr=stderr)

    monkeypatch.setattr(systemd_user.shutil, "which", lambda _name: "/usr/bin/systemd-run")
    monkeypatch.setattr(systemd_user.subprocess, "run", trial)

    assert systemd_user.get_sandbox_error(["NoNewPrivileges=yes", "PrivateUsers=yes"]) == expected
    assert trials[0][:2] == ["systemd-run", "--user"]
    assert trials[0][-5:] == ["-p", "NoNewPrivileges=yes", "-p", "PrivateUsers=yes", "true"]


def test_the_probe_needs_systemd_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(systemd_user.shutil, "which", lambda _name: None)
    assert "systemd-run" in systemd_user.get_sandbox_error(["NoNewPrivileges=yes"])


def _sandboxed_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe_error: str, sandbox: bool = True
):
    from pilot.managers.processes import systemd

    bench = make_bench(tmp_path / "benches" / "test-bench")
    bench.config.production = ProductionConfig(enabled=True, process_manager="systemd", sandbox=sandbox)
    manager = systemd.SystemdProcessManager(bench)
    manager.systemd_conf_dir.mkdir(parents=True, exist_ok=True)
    web = ProcessDefinition("web", ["/env/bin/python", "serve"], bench.logs_path / "web.log")
    monkeypatch.setattr(systemd, "AdminEnvManager", MagicMock())
    monkeypatch.setattr(systemd, "get_sandbox_error", lambda _properties: probe_error)
    monkeypatch.setattr(manager, "_prod_process_definitions", lambda: [web])
    return manager


def test_write_config_sandboxes_workload_units(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _sandboxed_manager(tmp_path, monkeypatch, probe_error="")
    manager.write_config()

    unit = (manager.systemd_conf_dir / "test-bench-web.service").read_text()
    assert f"TemporaryFileSystem={tmp_path / 'benches'}:ro\n" in unit
    assert f"BindPaths={tmp_path / 'benches' / 'test-bench'}\n" in unit


def test_a_failed_probe_keeps_the_current_units(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _sandboxed_manager(tmp_path, monkeypatch, probe_error="Unknown assignment: PrivateUsers=yes")
    existing = manager.systemd_conf_dir / "test-bench-web.service"
    existing.write_text("[Service]\nExecStart=/env/bin/python serve\n")

    with pytest.raises(BenchError, match="Unknown assignment: PrivateUsers=yes"):
        manager.write_config()

    assert existing.read_text() == "[Service]\nExecStart=/env/bin/python serve\n"


def test_write_config_skips_the_sandbox_and_trial_when_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _sandboxed_manager(tmp_path, monkeypatch, probe_error="must not run", sandbox=False)
    manager.write_config()

    assert "NoNewPrivileges" not in (manager.systemd_conf_dir / "test-bench-web.service").read_text()
