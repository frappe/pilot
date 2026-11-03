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
    "ProtectHome=tmpfs",
    "PrivateTmp=yes",
    "TemporaryFileSystem=/srv/pilot/benches:ro",
    "BindPaths=/srv/pilot/benches/b",
    "ReadWritePaths=/srv/pilot/benches/b",
    f"BindReadOnlyPaths=-/srv/pilot/benches/b/config -{Path.home()}/.local/share/uv/python -{Path.home()}/.nvm",
    "InaccessiblePaths=/srv/pilot/benches/b/bench.toml",
    f"TemporaryFileSystem={Path.home()}:ro",
}


def test_the_sandbox_directives(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NVM_DIR", raising=False)
    assert set(get_sandbox_properties(BENCH, BENCHES)) == SANDBOX_DIRECTIVES


def test_home_is_masked_not_hidden_path_by_path() -> None:
    properties = get_sandbox_properties(BENCH, BENCHES)

    assert "ProtectHome=read-only" not in properties
    assert not any(str(Path.home()) in line and "Inaccessible" in line for line in properties)


def test_nvm_dir_and_extra_paths_are_bound_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVM_DIR", "/opt/nvm")
    properties = get_sandbox_properties(BENCH, BENCHES, read_paths=("/home/u/app",), write_paths=("/home/u/data",))

    read_only = next(line for line in properties if line.startswith("BindReadOnlyPaths="))
    assert "-/opt/nvm" in read_only
    assert "-/home/u/app" in read_only
    assert "BindPaths=-/home/u/data" in properties
    assert "ReadWritePaths=-/home/u/data" in properties


def test_a_process_gets_its_own_paths_on_top_of_the_shared_ones() -> None:
    definition = _definition()
    definition.read_paths = ["/home/u/models"]
    definition.write_paths = ["/home/u/state"]

    service = SystemdRenderer("b", sandbox_properties=["NoNewPrivileges=yes"]).render(definition)

    assert "BindReadOnlyPaths=-/home/u/models\n" in service
    assert "BindPaths=-/home/u/state\n" in service
    other = SystemdRenderer("b", sandbox_properties=["NoNewPrivileges=yes"]).render(_definition("worker"))
    assert "/home/u/models" not in other


def test_an_unsandboxed_unit_ignores_declared_paths() -> None:
    definition = _definition()
    definition.read_paths = ["/home/u/models"]

    assert "/home/u/models" not in SystemdRenderer("b").render(definition)


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


def test_the_probe_names_blocked_user_namespaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ubuntu 24.04 blocks them through AppArmor; systemd-run then exits 226 and prints nothing."""
    monkeypatch.setattr(systemd_user.shutil, "which", lambda _name: "/usr/bin/systemd-run")
    monkeypatch.setattr(
        systemd_user.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=226, stderr="")
    )

    error = systemd_user.get_sandbox_error(["PrivateUsers=yes"])
    assert "user namespaces" in error
    assert "kernel.apparmor_restrict_unprivileged_userns" in error


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
    monkeypatch.setattr(manager, "get_sandbox_paths", lambda: ((), ()))
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


def test_sandbox_paths_gather_install_linked_apps_and_active_app_declarations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pilot.core.app.sandbox import SandboxPaths
    from pilot.managers.processes import systemd

    manager = _sandboxed_manager(tmp_path, monkeypatch, probe_error="")
    monkeypatch.undo()
    linked = SimpleNamespace(is_linked=True, path=tmp_path / "linked", module_name="linked", processes=None)
    declared = SimpleNamespace(
        is_linked=False,
        path=tmp_path / "mail",
        module_name="mail",
        processes=SimpleNamespace(get_sandbox_paths=lambda: SandboxPaths(read=("/r",), write=("/w",))),
    )
    idle = SimpleNamespace(
        is_linked=False,
        path=tmp_path / "idle",
        module_name="idle",
        processes=SimpleNamespace(get_sandbox_paths=lambda: SandboxPaths(read=("/idle",))),
    )
    manager.bench = SimpleNamespace(
        apps=lambda: [linked, declared, idle], processes=SimpleNamespace(active_apps={"mail"})
    )
    monkeypatch.setattr(systemd, "cli_root", lambda: Path("/opt/pilot"))

    read, write = manager.get_sandbox_paths()

    assert read == ("/opt/pilot", str((tmp_path / "linked").resolve()), "/r")
    assert write == ("/w",)


def test_control_files_are_read_only_or_hidden_from_workloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NVM_DIR", raising=False)
    properties = get_sandbox_properties(BENCH, BENCHES)

    read_only = next(line for line in properties if line.startswith("BindReadOnlyPaths="))
    assert f"-{BENCH}/config" in read_only
    assert f"InaccessiblePaths={BENCH}/bench.toml" in properties
    assert not any(line.startswith("ReadWritePaths=") and "config" in line for line in properties)


def test_the_real_home_is_masked_even_when_it_is_not_under_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: Path("/srv/frappe"))

    assert "TemporaryFileSystem=/srv/frappe:ro" in get_sandbox_properties(BENCH, BENCHES)
