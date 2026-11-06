from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pilot.config.production import ProductionConfig
from pilot.exceptions import BenchError
from pilot.managers import systemd_user
from pilot.managers.processes.definitions import ProcessDefinition
from pilot.managers.processes.sandbox import CONTROL_ENTRIES, WORKLOAD_DIRECTORIES
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
FOLDERS = " ".join(f"{BENCH}/{name}" for name in WORKLOAD_DIRECTORIES)

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
    "TemporaryFileSystem=/run:ro",
    "TemporaryFileSystem=/dev/shm",
    "InaccessiblePaths=-/var/log/journal",
    "TemporaryFileSystem=/srv/pilot/benches:ro",
    f"BindPaths={FOLDERS}",
    f"ReadWritePaths={FOLDERS}",
    f"TemporaryFileSystem={Path.home()}:ro",
    "BindReadOnlyPaths="
    + " ".join(f"-{BENCH}/config/{name}" for name in CONTROL_ENTRIES)
    + f" -{BENCH}/sites/common_site_config.json"
    + " -/run/systemd/resolve",
}


def test_the_sandbox_directives() -> None:
    assert set(get_sandbox_properties(BENCH, BENCHES)) == SANDBOX_DIRECTIVES


def test_home_is_masked_not_hidden_path_by_path() -> None:
    properties = get_sandbox_properties(BENCH, BENCHES)

    assert "ProtectHome=read-only" not in properties
    assert not any(str(Path.home()) in line and "Inaccessible" in line for line in properties)


def test_extra_paths_are_bound_next_to_the_control_files() -> None:
    properties = get_sandbox_properties(BENCH, BENCHES, read_paths=("/home/u/app",), write_paths=("/home/u/data",))

    read_only = next(line for line in properties if line.startswith("BindReadOnlyPaths="))
    assert read_only.endswith(" -/home/u/app")
    assert read_only.startswith(f"BindReadOnlyPaths=-{BENCH}/config/{CONTROL_ENTRIES[0]} ")
    assert "BindPaths=-/home/u/data" in properties
    assert "ReadWritePaths=-/home/u/data" in properties


def test_the_real_home_is_masked_even_when_it_is_not_under_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: Path("/srv/frappe"))

    assert "TemporaryFileSystem=/srv/frappe:ro" in get_sandbox_properties(BENCH, BENCHES)


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

    assert 'BindPaths="/srv/my benches/b/apps" ' in " ".join(properties)
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
    bench = tmp_path / "benches" / "test-bench"
    assert f"BindPaths={' '.join(str(bench / name) for name in WORKLOAD_DIRECTORIES)}\n" in unit


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


def test_sandbox_paths_gather_tier_one_linked_apps_and_active_app_declarations(
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
    monkeypatch.setattr(systemd.PilotSandboxPaths, "read", ["/tier-one"])

    read, write = manager.get_sandbox_paths()

    assert read == ("/tier-one", str((tmp_path / "linked").resolve()), "/r")
    assert write == ("/w",)


def test_control_files_are_read_only_or_hidden_from_workloads() -> None:
    properties = get_sandbox_properties(BENCH, BENCHES)

    read_only = next(line for line in properties if line.startswith("BindReadOnlyPaths="))
    for name in ("services", "nginx", "admin-gunicorn.conf.py", "gunicorn.conf.py"):
        assert f"-{BENCH}/config/{name}" in read_only
    assert f"-{BENCH}/sites/common_site_config.json" in read_only
    writable = next(line for line in properties if line.startswith("ReadWritePaths="))
    assert not any(f"{BENCH}/config/{name}" in writable for name in CONTROL_ENTRIES)


def test_only_the_workload_folders_of_the_bench_are_mounted() -> None:
    mounted = " ".join(line.split("=", 1)[1] for line in get_sandbox_properties(BENCH, BENCHES)).split()
    bench_paths = [Path(path.lstrip("-")) for path in mounted if path.lstrip("-").startswith(f"{BENCH}/")]

    assert str(BENCH) not in mounted
    assert {path.relative_to(BENCH).parts[0] for path in bench_paths} <= set(WORKLOAD_DIRECTORIES)
    for admin_file in ("bench.toml", ".credentials", "tasks", ".patches.json", ".active-jtis.json"):
        assert admin_file not in WORKLOAD_DIRECTORIES


def test_config_is_not_bound_as_a_whole_so_frappe_can_create_its_lock_files() -> None:
    properties = get_sandbox_properties(BENCH, BENCHES)
    read_only = next(line for line in properties if line.startswith("BindReadOnlyPaths="))

    assert f"-{BENCH}/config " not in read_only + " "
    assert not any(line.endswith(f"{BENCH}/config") for line in properties)


def test_host_sockets_logs_and_shared_memory_stay_outside_the_sandbox() -> None:
    properties = get_sandbox_properties(BENCH, BENCHES)
    read_only = next(line for line in properties if line.startswith("BindReadOnlyPaths="))

    assert "TemporaryFileSystem=/run:ro" in properties
    assert "-/run/systemd/resolve" in read_only.split()
    assert "TemporaryFileSystem=/dev/shm" in properties
    assert "InaccessiblePaths=-/var/log/journal" in properties
