from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.bench import Bench
from tests.pilot.commands.test_commands import make_bench


def _write_venv(bench: Bench, cfg: str) -> None:
    bench.env_path.mkdir(parents=True, exist_ok=True)
    (bench.env_path / "pyvenv.cfg").write_text(cfg)


@pytest.mark.parametrize(
    "cfg, expected",
    [
        ("home = /usr/bin\nversion_info = 3.14.2\n", (3, 14)),  # uv
        ("home = /usr/bin\nversion = 3.10.12\n", (3, 10)),  # python -m venv
        ("home = /usr/bin\n", ()),
    ],
)
def test_the_venv_python_version_comes_from_pyvenv_cfg(tmp_path: Path, cfg: str, expected: tuple) -> None:
    bench = make_bench(tmp_path)
    _write_venv(bench, cfg)

    assert bench.python_version == expected


def test_no_venv_means_an_unknown_version(tmp_path: Path) -> None:
    assert make_bench(tmp_path).python_version == ()


@pytest.mark.parametrize("version, safe", [("3.14.2", True), ("3.11.0", True), ("3.10.12", False)])
def test_the_bench_python_leaves_the_working_directory_off_sys_path(
    tmp_path: Path, version: str, safe: bool
) -> None:
    bench = make_bench(tmp_path)
    _write_venv(bench, f"version_info = {version}\n")

    assert bench.python_argv == [str(bench.python), *(["-P"] if safe else [])]
    assert bench.frappe_call == [*bench.python_argv, "-m", "frappe.utils.bench_helper"]


def test_a_package_planted_in_sites_does_not_run(tmp_path: Path) -> None:
    """A real Python, run the way Pilot runs frappe_call, does not import a sites/frappe/ package."""
    bench = make_bench(tmp_path)
    (bench.env_path / "bin").mkdir(parents=True)
    (bench.env_path / "bin" / "python").symlink_to(sys.executable)
    _write_venv(bench, f"version_info = {sys.version_info.major}.{sys.version_info.minor}.0\n")
    planted = bench.sites_path / "frappe"
    (planted / "utils").mkdir(parents=True)
    marker = tmp_path / "planted-code-ran"
    (planted / "__init__.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
    (planted / "utils" / "__init__.py").write_text("")
    (planted / "utils" / "bench_helper.py").write_text("")

    subprocess.run([*bench.frappe_call, "frappe", "--version"], cwd=bench.sites_path, capture_output=True)
    subprocess.run([*bench.python_argv, "-c", "import frappe"], cwd=bench.sites_path, capture_output=True)

    assert not marker.exists()


def test_the_console_runs_the_bench_python_with_its_safe_argv(tmp_path: Path) -> None:
    from pilot.core.site.console import completions

    bench = make_bench(tmp_path)
    _write_venv(bench, "version_info = 3.14.2\n")
    site = type("Site", (), {"bench": bench, "config": type("Config", (), {"name": "site1"})()})()
    with patch.object(completions, "run_command") as run_command:
        run_command.return_value.stdout = b"[]"
        _ = completions.ConsoleCompletions(site).doctypes

    assert run_command.call_args.args[0][:2] == [str(bench.python), "-P"]


def test_setting_the_admin_password_runs_the_bench_python_with_its_safe_argv(tmp_path: Path) -> None:
    from pilot.core.site import commands

    bench = make_bench(tmp_path)
    _write_venv(bench, "version_info = 3.14.2\n")
    with patch.object(commands, "run_command") as run_command:
        commands.SiteCommands(bench.site("site1")).set_admin_password("secret")

    assert run_command.call_args.args[0][:2] == [str(bench.python), "-P"]
    assert run_command.call_args.kwargs["cwd"] == bench.sites_path
