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


def _sandboxed_bench(tmp_path: Path) -> Bench:
    """A bench with a real Python whose web unit runs in the sandbox."""
    bench = make_bench(tmp_path)
    bench.config.production.process_manager = "systemd"
    (bench.env_path / "bin").mkdir(parents=True)
    (bench.env_path / "bin" / "python").symlink_to(sys.executable)
    _write_venv(bench, f"version_info = {sys.version_info.major}.{sys.version_info.minor}.0\n")
    services = bench.config_path / "services"
    services.mkdir(parents=True)
    (services / f"{bench.config.name}-web.service").write_text("[Service]\nPrivateUsers=yes\n")
    (bench.sites_path / "site1" / "logs").mkdir(parents=True)
    (bench.path / "logs").mkdir(exist_ok=True)
    return bench


# What Frappe's logger does in sites/: one file in the bench logs, one in the site logs.
_LOG_TWICE = (
    "import logging.handlers as h\n"
    "for path in ('../logs/frappe.log', 'site1/logs/frappe.log'):\n"
    "    h.RotatingFileHandler(path, maxBytes=100_000, backupCount=2).emit(\n"
    "        __import__('logging').makeLogRecord({'msg': 'migrated'}))\n"
)


def test_a_sandboxed_bench_starts_python_through_the_log_guard(tmp_path: Path) -> None:
    bench = _sandboxed_bench(tmp_path)

    assert bench.python_argv[:2] == [str(bench.python), "-P"]
    assert bench.python_argv[2].endswith("unsandboxed_entry.py")
    assert bench.python_argv[3] == str(bench.path)


def test_the_log_guard_writes_logs_that_are_plain_files(tmp_path: Path) -> None:
    bench = _sandboxed_bench(tmp_path)

    result = subprocess.run([*bench.python_argv, "-c", _LOG_TWICE], cwd=bench.sites_path, capture_output=True)

    assert result.returncode == 0, result.stderr
    assert (bench.path / "logs" / "frappe.log").read_text() == "migrated\n"
    assert (bench.sites_path / "site1" / "logs" / "frappe.log").read_text() == "migrated\n"


@pytest.mark.parametrize("planted", ["file", "folder"])
def test_the_log_guard_refuses_a_link_the_workload_planted(tmp_path: Path, planted: str) -> None:
    bench = _sandboxed_bench(tmp_path)
    target = tmp_path / "bashrc"
    target.write_text("original\n")
    logs = bench.sites_path / "site1" / "logs"
    if planted == "file":
        (logs / "frappe.log").symlink_to(target)
    else:
        logs.rmdir()
        logs.symlink_to(tmp_path)
        target = tmp_path / "frappe.log"

    result = subprocess.run([*bench.python_argv, "-c", _LOG_TWICE], cwd=bench.sites_path, capture_output=True)

    assert result.returncode != 0
    assert not target.exists() or "migrated" not in target.read_text()


def test_the_log_guard_runs_a_module_with_its_arguments(tmp_path: Path) -> None:
    bench = _sandboxed_bench(tmp_path)

    result = subprocess.run(
        [*bench.python_argv, "-c", "import sys; print(sys.argv)", "a", "b"],
        cwd=bench.sites_path,
        capture_output=True,
        text=True,
    )
    module = subprocess.run(
        [*bench.python_argv, "-m", "json.tool", "--compact"],
        cwd=bench.sites_path,
        input='{"a": 1}',
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "['-c', 'a', 'b']"
    assert module.stdout.strip() == '{"a":1}'


def test_a_package_planted_in_sites_does_not_run_through_the_log_guard(tmp_path: Path) -> None:
    bench = _sandboxed_bench(tmp_path)
    marker = tmp_path / "planted-code-ran"
    (bench.sites_path / "json").mkdir()
    (bench.sites_path / "json" / "__init__.py").write_text(f"open({str(marker)!r}, 'w').close()\n")

    subprocess.run([*bench.python_argv, "-c", "import json"], cwd=bench.sites_path, capture_output=True)

    assert not marker.exists()
