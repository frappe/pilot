from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from pilot.core.server.monitoring_config import MonitorConfigurator
from pilot.exceptions import BenchError
from pilot.patches import run_monitor_as_service
from pilot.patches.run_monitor_as_service import run

LEGACY_UNITS = ("pilot-monitor.timer", "pilot-uptime.timer", "pilot-uptime.service")


def _bench(root: Path, name: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "bench.toml").write_text(f'[bench]\nname = "{name}"\npython = "3.14"\n')


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A host on the timers. pilot-monitor.service is enabled and active once an install succeeds."""
    unit_dir = tmp_path / "user_units"
    unit_dir.mkdir()
    for name in (*LEGACY_UNITS, "pilot-monitor.service"):
        (unit_dir / name).write_text("")
    state = SimpleNamespace(
        unit_dir=unit_dir, root=tmp_path / "benches", installs=[], starts=True, active=False, enabled=False
    )

    def install(self) -> None:
        state.installs.append("install")
        state.active, state.enabled = state.starts, True

    def systemctl(command: list[str], **kwargs) -> SimpleNamespace:
        if "enable" in command:
            state.enabled = True
        checks = {"is-enabled": state.enabled, "is-active": state.active}
        return SimpleNamespace(
            returncode=next((0 if ok else 3 for check, ok in checks.items() if check in command), 0)
        )

    monkeypatch.setattr("pilot.managers.systemd_user.user_unit_dir", lambda: unit_dir)
    monkeypatch.setattr("pilot.utils.cli_root", lambda: tmp_path)
    monkeypatch.setattr("subprocess.run", systemctl)
    monkeypatch.setattr(MonitorConfigurator, "install", install)
    monkeypatch.setattr(MonitorConfigurator, "restart", lambda self: None)
    _bench(state.root, "one")
    _bench(state.root, "two")
    return state


def test_timers_are_replaced_by_the_monitor_service_once(host: SimpleNamespace) -> None:
    run(host.root)
    run(host.root)

    assert host.installs == ["install"]
    assert sorted(path.name for path in host.unit_dir.iterdir()) == ["pilot-monitor.service"]


def test_a_failed_install_is_retried_on_the_next_run(
    host: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = MonitorConfigurator.install

    def fail_once(self) -> None:
        host.installs.append("failed")
        monkeypatch.setattr(MonitorConfigurator, "install", install)
        raise BenchError("daemon-reload failed")

    monkeypatch.setattr(MonitorConfigurator, "install", fail_once)
    with pytest.raises(BenchError):
        run(host.root)
    run(host.root)

    assert host.installs == ["failed", "install"]


def test_a_service_that_does_not_start_is_not_marked_applied(host: SimpleNamespace) -> None:
    host.starts = False

    with pytest.raises(BenchError, match="is not enabled and running"):
        run(host.root)

    assert run_monitor_as_service._is_replacement_needed()


def test_a_running_service_that_was_never_enabled_is_enabled(
    host: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in LEGACY_UNITS:
        (host.unit_dir / name).unlink()
    host.active = True
    # An earlier run wrote the current unit, so install() has nothing to do.
    monkeypatch.setattr(MonitorConfigurator, "install", lambda self: None)

    run(host.root)

    assert host.enabled
