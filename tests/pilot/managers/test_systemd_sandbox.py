from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pilot.config.sandbox import SandboxConfig
from pilot.managers import systemd_user
from pilot.managers.processes.definitions import ProcessDefinition
from pilot.managers.processes.systemd_render import SystemdRenderer, get_sandbox_properties


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

GROUP_DIRECTIVES = {
    "syscalls": {
        "SystemCallFilter=@system-service",
        "SystemCallArchitectures=native",
        "SystemCallErrorNumber=EPERM",
    },
    "kernel": {
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectKernelLogs=yes",
        "ProtectControlGroups=yes",
        "ProtectClock=yes",
        "ProtectHostname=yes",
    },
    "devices": {"PrivateDevices=yes"},
    "namespaces": {
        "RestrictNamespaces=yes",
        "RestrictRealtime=yes",
        "RestrictSUIDSGID=yes",
        "LockPersonality=yes",
    },
    "network": {"RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK"},
    "filesystem": {
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        "PrivateTmp=yes",
        "TemporaryFileSystem=/srv/pilot/benches:ro",
        "BindPaths=/srv/pilot/benches/b",
        "ReadWritePaths=/srv/pilot/benches/b",
        f"InaccessiblePaths=-{Path.home()}/.ssh -{Path.home()}/.config/systemd",
    },
}


def _properties(**sandbox: object) -> set[str]:
    config = SandboxConfig.from_dict({"enabled": True, **sandbox})
    return set(get_sandbox_properties(config, BENCH, BENCHES))


def test_full_sandbox_is_the_baseline_plus_every_group() -> None:
    expected = {"NoNewPrivileges=yes", "PrivateUsers=yes"}.union(*GROUP_DIRECTIVES.values())
    assert _properties() == expected


@pytest.mark.parametrize("group", sorted(GROUP_DIRECTIVES))
def test_switching_a_group_off_drops_exactly_its_directives(group: str) -> None:
    assert _properties() - _properties(**{group: False}) == GROUP_DIRECTIVES[group]


def test_baseline_stays_with_every_group_off() -> None:
    assert _properties(**dict.fromkeys(GROUP_DIRECTIVES, False)) == {
        "NoNewPrivileges=yes",
        "PrivateUsers=yes",
    }


def test_disabled_sandbox_has_no_properties() -> None:
    assert get_sandbox_properties(SandboxConfig(), BENCH, BENCHES) == []


def test_extra_paths_expand_home_and_quote_spaces() -> None:
    properties = _properties(writable_paths=["~/shared data"], inaccessible_paths=["/srv/keys"])

    assert f'ReadWritePaths=/srv/pilot/benches/b "{Path.home()}/shared data"' in properties
    assert f"InaccessiblePaths=-{Path.home()}/.ssh -{Path.home()}/.config/systemd /srv/keys" in properties


def test_extra_hidden_paths_apply_without_the_filesystem_group() -> None:
    assert _properties(filesystem=False, inaccessible_paths=["/srv/keys"]) >= {"InaccessiblePaths=/srv/keys"}


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
