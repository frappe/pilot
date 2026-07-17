from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.commands.remove.production import RemoveProductionCommand
from pilot.config.bench_config import BenchConfig
from pilot.core.bench import Bench


def _make_bench(tmp_path: Path, *, enabled: bool, pm: str = "systemd") -> Bench:
    bench_dir = tmp_path / "benches" / "prod"
    (bench_dir / "sites").mkdir(parents=True, exist_ok=True)
    prod = f'[production]\nenabled = {"true" if enabled else "false"}\n'
    if enabled:
        prod += f'process_manager = "{pm}"\n'
    (bench_dir / "bench.toml").write_text(
        '[bench]\nname = "prod"\npython = "3.14"\n\n'
        '[[apps]]\nname = "frappe"\nrepo = "r"\nbranch = "develop"\n\n'
        '[mariadb]\nroot_password = "root"\n\n'
        '[redis]\ncache_port = 13000\nqueue_port = 11000\n\n'
        '[admin]\ndomain = "admin-prod.example.com"\ntls = true\n\n'
        + prod
    )
    return Bench(BenchConfig.from_file(bench_dir / "bench.toml"), bench_dir)


def test_remove_noop_when_not_enabled(tmp_path: Path, capsys) -> None:
    bench = _make_bench(tmp_path, enabled=False)
    RemoveProductionCommand(bench).run()
    assert "not deployed to production" in capsys.readouterr().out


def test_remove_disables_keeps_domain(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, enabled=True, pm="systemd")
    with patch("pilot.managers.processes.systemd.SystemdProcessManager") as Sys, \
         patch("pilot.managers.nginx.NginxManager") as Nginx:
        Sys.return_value = MagicMock()
        Nginx.return_value = MagicMock()
        RemoveProductionCommand(bench).run()
        Sys.return_value.remove_units.assert_called_once()
        Nginx.return_value.uninstall_config.assert_called_once()
    data = tomllib.loads((bench.path / "bench.toml").read_text())
    assert data["production"]["enabled"] is False
    assert "process_manager" not in data["production"]
    assert data["admin"]["domain"] == "admin-prod.example.com"
