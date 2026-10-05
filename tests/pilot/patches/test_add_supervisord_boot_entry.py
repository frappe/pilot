from __future__ import annotations

from pathlib import Path

import pytest

from pilot.managers.processes.supervisor import SupervisorProcessManager
from pilot.patches.add_supervisord_boot_entry import run

_TOML = """[bench]
name = "{name}"
python = "3.14"

[[apps]]
name = "frappe"
repo = "https://github.com/frappe/frappe"
branch = "version-16"

[production]
enabled = true
process_manager = "{manager}"

[admin]
domain = "admin.example.com"
"""


@pytest.fixture
def installed(monkeypatch) -> list[str]:
    benches: list[str] = []
    monkeypatch.setattr(SupervisorProcessManager, "is_configured", lambda self: True)
    monkeypatch.setattr(SupervisorProcessManager, "install_config", lambda self: benches.append(self.bench.config.name))
    return benches


def _bench(root: Path, name: str, manager: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "bench.toml").write_text(_TOML.format(name=name, manager=manager))


def test_only_supervisor_benches_get_the_boot_entry_once(tmp_path: Path, installed: list[str]) -> None:
    root = tmp_path / "benches"
    _bench(root, "one", "supervisor")
    _bench(root, "two", "systemd")

    run(root)
    run(root)

    assert installed == ["one"]
