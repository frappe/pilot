from __future__ import annotations

from pathlib import Path

import pytest

from pilot.managers.processes.systemd import SystemdProcessManager
from pilot.patches.rewrite_database_wait_in_units import run

_TOML = """[bench]
name = "{name}"
python = "3.14"

[[apps]]
name = "frappe"
repo = "https://github.com/frappe/frappe"
branch = "version-16"

[production]
enabled = {production}
process_manager = "{manager}"

[admin]
domain = "admin-{name}.example.com"
"""


@pytest.fixture
def calls(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(SystemdProcessManager, "is_configured", lambda self: True)
    monkeypatch.setattr(
        SystemdProcessManager, "write_config", lambda self: calls.append(f"units:{self.bench.config.name}")
    )
    monkeypatch.setattr(SystemdProcessManager, "reload_manager_config", lambda self: calls.append("reload"))
    return calls


def _bench(root: Path, name: str, production: bool = True, manager: str = "systemd") -> None:
    (root / name).mkdir(parents=True)
    (root / name / "bench.toml").write_text(
        _TOML.format(name=name, production=str(production).lower(), manager=manager)
    )


def test_a_production_systemd_bench_gets_its_units_rewritten_and_reloaded(tmp_path: Path, calls: list[str]) -> None:
    _bench(tmp_path, "one")

    run(tmp_path)

    assert calls == ["units:one", "reload"]


def test_other_benches_are_left_alone(tmp_path: Path, calls: list[str]) -> None:
    _bench(tmp_path, "dev", production=False, manager="")
    _bench(tmp_path, "super", manager="supervisor")

    run(tmp_path)

    assert calls == []


def test_the_patch_runs_once_per_bench(tmp_path: Path, calls: list[str]) -> None:
    _bench(tmp_path, "one")

    run(tmp_path)
    run(tmp_path)

    assert calls == ["units:one", "reload"]


def test_a_bench_that_is_not_set_up_in_systemd_is_marked_done_without_a_rewrite(
    tmp_path: Path, calls: list[str], monkeypatch
) -> None:
    _bench(tmp_path, "one")
    monkeypatch.setattr(SystemdProcessManager, "is_configured", lambda self: False)

    run(tmp_path)

    assert calls == []
