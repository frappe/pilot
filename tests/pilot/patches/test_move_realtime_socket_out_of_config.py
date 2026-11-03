from __future__ import annotations

from pathlib import Path

import pytest

from pilot.managers.nginx import NginxManager
from pilot.managers.processes.systemd import SystemdProcessManager
from pilot.patches.move_realtime_socket_out_of_config import run

_TOML = """[bench]
name = "{name}"
python = "3.14"

[[apps]]
name = "frappe"
repo = "https://github.com/frappe/frappe"
branch = "version-16"

[production]
enabled = {production}
process_manager = "systemd"

[admin]
domain = "admin-{name}.example.com"
"""


@pytest.fixture
def calls(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(SystemdProcessManager, "is_configured", lambda self: True)
    monkeypatch.setattr(SystemdProcessManager, "write_config", lambda self: calls.append(f"units:{self.bench.config.name}"))
    monkeypatch.setattr(SystemdProcessManager, "reload_manager_config", lambda self: None)
    monkeypatch.setattr("pilot.utils.run_command", lambda argv, env: calls.append(argv[-1]))
    monkeypatch.setattr(NginxManager, "reload_for_site_change", lambda self: calls.append("nginx"))
    return calls


def _bench(root: Path, name: str, production: bool) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "bench.toml").write_text(_TOML.format(name=name, production=str(production).lower()))
    (root / name / "apps" / "frappe").mkdir(parents=True)
    (root / name / "apps" / "frappe" / "node_utils.js").write_text("process.env.FRAPPE_SOCKETIO_UDS")


def test_production_benches_move_the_realtime_socket_out_of_config_once(tmp_path: Path, calls: list[str]) -> None:
    root = tmp_path / "benches"
    _bench(root, "one", production=True)
    _bench(root, "two", production=False)

    run(root)
    run(root)

    assert calls == ["units:one", "one-socketio.service", "nginx"]
