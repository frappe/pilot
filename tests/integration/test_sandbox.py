"""A sandboxed systemd production bench: its units run, and the sandbox blocks what it should."""

from __future__ import annotations

import json
import os
import subprocess
import time
import tomllib
from pathlib import Path

import pytest

from pilot.config import BenchConfig
from pilot.config.common_site_config import update_common_site_config
from pilot.core.bench import Bench
from pilot.managers.processes.sandbox import PilotSandboxPaths
from pilot.managers.processes.systemd import SystemdProcessManager
from pilot.managers.systemd_user import systemctl_env
from pilot.utils import cli_root

SITE = "site1.localhost"
ADMIN_DOMAIN = "bench.localhost"
NEIGHBOUR = "neighbour-bench"

pytestmark = [pytest.mark.integration, pytest.mark.production]


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, env=systemctl_env())


def _systemctl_value(unit: str, prop: str) -> str:
    return _run("systemctl", "--user", "show", "-p", prop, "--value", unit).stdout.strip()


def _workload_units(bench_root: Path) -> list[str]:
    services = sorted((bench_root / "config" / "services").glob("*.service"))
    return [
        path.name
        for path in services
        if not path.name.endswith(("-admin.service", "-central-bootstrap.service"))
    ]


@pytest.fixture(scope="module", autouse=True)
def _require_opt_in() -> None:
    if os.environ.get("BENCH_E2E_SANDBOX") != "1":
        pytest.skip("BENCH_E2E_SANDBOX != 1 (destructive: installs services, rewrites nginx)")


@pytest.fixture(scope="module")
def sandboxed(bench_root: Path, bench_bin: str):
    toml_path = bench_root / "bench.toml"
    data = tomllib.loads(toml_path.read_text())
    data.setdefault("admin", {}).update(password="admin", tls=False)
    data.setdefault("production", {})["sandbox"] = True
    BenchConfig.write_raw(toml_path, data)
    neighbour = bench_root.parent / NEIGHBOUR
    neighbour.mkdir(exist_ok=True)
    (neighbour / "secret.txt").write_text("secret")

    result = _run(
        bench_bin,
        "setup",
        "production",
        "--process-manager",
        "systemd",
        "--admin-domain",
        ADMIN_DOMAIN,
        cwd=bench_root,
    )
    assert result.returncode == 0, f"sandboxed setup production failed:\n{result.stdout}\n{result.stderr}"
    name = data["bench"]["name"]
    print(_run("systemd-analyze", "--user", "security", "--no-pager", f"{name}-web.service").stdout)

    yield bench_root

    _run(bench_bin, "remove", "production", cwd=bench_root)


def test_the_site_answers(sandboxed: Path) -> None:
    command = ["curl", "-s", "--resolve", f"{SITE}:80:127.0.0.1", f"http://{SITE}/api/method/frappe.ping"]
    body = ""
    for _ in range(30):
        body = _run(*command).stdout
        if "pong" in body:
            return
        time.sleep(2)
    pytest.fail(f"site did not answer ping: {body!r}")


def test_every_workload_unit_runs_sandboxed(sandboxed: Path) -> None:
    units = _workload_units(sandboxed)
    assert any(unit.endswith("-web.service") for unit in units), units
    for unit in units:
        assert _systemctl_value(unit, "ActiveState") == "active", unit
        status = Path(f"/proc/{_systemctl_value(unit, 'MainPID')}/status").read_text()
        assert "NoNewPrivs:\t1" in status, unit
        assert "Seccomp:\t2" in status, unit


def test_the_admin_unit_is_not_sandboxed(sandboxed: Path) -> None:
    admin = next((sandboxed / "config" / "services").glob("*-admin.service"))
    assert "NoNewPrivileges" not in admin.read_text()


def test_the_web_process_mounts_a_tmpfs_over_the_benches_directory(sandboxed: Path) -> None:
    web = next(unit for unit in _workload_units(sandboxed) if unit.endswith("-web.service"))
    mountinfo = Path(f"/proc/{_systemctl_value(web, 'MainPID')}/mountinfo").read_text().splitlines()
    # Field 5 is the mount point; the fstype follows the " - " separator.
    mounts = {line.split()[4]: line.split(" - ")[1].split()[0] for line in mountinfo}
    assert mounts.get(str(sandboxed.parent)) == "tmpfs", mounts
    assert str(sandboxed / "sites") in mounts, mounts
    assert str(sandboxed) not in mounts, mounts


def test_pilot_rewrites_keep_files_away_from_a_running_unit(sandboxed: Path) -> None:
    web = next(unit for unit in _workload_units(sandboxed) if unit.endswith("-web.service"))
    pid = _systemctl_value(web, "MainPID")
    toml_path = sandboxed / "bench.toml"
    BenchConfig.write_raw(toml_path, tomllib.loads(toml_path.read_text()))
    aliases = Bench(sandboxed).site_aliases
    before = aliases.registry_path.read_text()
    aliases.record("old.example.com")
    aliases.registry_path.write_text(before)
    with update_common_site_config(sandboxed / "sites"):
        pass

    assert not Path(f"/proc/{pid}/root{toml_path}").exists()
    mountinfo = Path(f"/proc/{pid}/mountinfo").read_text()
    assert f" {aliases.registry_path} " in mountinfo
    assert f" {sandboxed / 'sites' / 'common_site_config.json'} " in mountinfo


@pytest.fixture(scope="module")
def run_sandboxed(sandboxed: Path):
    """Run a shell line in a throwaway unit with the same sandbox lines as the bench's units."""
    properties = SystemdProcessManager(Bench(sandboxed)).get_checked_sandbox_properties()
    web = next((sandboxed / "config" / "services").glob("*-web.service")).read_text()
    assert all(f"{line}\n" in web for line in properties), "web unit and probe disagree"
    argv = ["systemd-run", "--user", "--wait", "--quiet", "--collect"]
    for directive in properties:
        argv += ["-p", directive]
    return lambda line: _run(*argv, "bash", "-c", line).returncode == 0


@pytest.mark.parametrize(
    "line",
    [
        "sudo -n true",
        "touch ~/.sandbox-escape",
        "touch ~/.config/systemd/user/sandbox-escape",
        "ls ~/.ssh",
        "cat ~/.ssh/id_ed25519",
        "test -e ~/.ssh",
        "test -e ~/.bashrc",
        "test -S /run/user/$(id -u)/bus",
        "test -S /run/dbus/system_bus_socket",
        "ls /var/log/journal",
        "systemd-run --user --pipe true",
        "touch {bench}/config/services/escape.service",
        "echo x >> {bench}/config/services/*-web.service",
        "echo x >> {bench}/config/admin-gunicorn.conf.py",
        "mkdir {bench}/config/nginx/planted",
        "mkdir {bench}/config/letsencrypt/planted",
        "cat {bench}/bench.toml",
        "test -e {bench}/bench.toml",
        "ls {bench}/tasks",
        "touch {bench}/planted",
        "mv {bench}/sites/common_site_config.json {bench}/sites/css.json",
        "echo x >> {bench}/sites/common_site_config.json",
        "ls {pilot}/databases/mariadb/data",
        "ls {pilot}/databases/mariadb/config",
        "ls {pilot}/.admin-venv",
        f"cat ../{NEIGHBOUR}/secret.txt",
        "unshare -U true",
    ],
)
def test_the_sandbox_blocks(run_sandboxed, sandboxed: Path, line: str) -> None:
    line = line.replace("../", f"{sandboxed.parent}/").replace("{bench}", str(sandboxed)).replace("{pilot}", str(cli_root()))
    assert not run_sandboxed(line), f"sandbox allowed: {line}"


def test_a_workload_cannot_change_the_unit_files_the_admin_loads(run_sandboxed, sandboxed: Path) -> None:
    """The units are symlinked into ~/.config/systemd/user, so a write there runs unsandboxed later."""
    units = sorted((sandboxed / "config" / "services").glob("*.service"))
    assert units
    before = {unit: unit.read_text() for unit in units}
    assert not run_sandboxed(f"echo escape >> {units[0]}")
    assert {unit: unit.read_text() for unit in units} == before


def test_a_workload_cannot_plant_code_the_admin_runs(run_sandboxed, sandboxed: Path) -> None:
    """The admin and the CLI run the bench venv and the apps outside the sandbox, such as for the console
    and backups. A .pth file in env/ runs on every start of that Python; a git hook in apps/ on a pull."""
    site_packages = next((sandboxed / "env" / "lib").glob("python*/site-packages"))
    hook = sandboxed / "apps" / "frappe" / ".git" / "hooks" / "post-merge"
    planted = [site_packages / "sandbox-escape.pth", hook]
    for path in planted:
        assert not run_sandboxed(f"echo 'import os' > {path}"), path
        assert not path.exists(), path


def test_a_package_planted_in_sites_does_not_replace_frappe_outside(run_sandboxed, sandboxed: Path) -> None:
    """Pilot runs the bench Python in sites/ outside the sandbox, such as for backups and the console."""
    bench = Bench(sandboxed)
    planted = sandboxed / "sites" / "frappe"
    marker = sandboxed / "sites" / "planted-frappe-ran"
    try:
        line = f"mkdir {planted} && echo \"open('{marker}', 'w').close()\" > {planted}/__init__.py"
        assert run_sandboxed(line), "the plant must succeed, or this test checks nothing"
        _run(*bench.frappe_call, "frappe", "--version", cwd=sandboxed / "sites")
        assert not marker.exists()
    finally:
        marker.unlink(missing_ok=True)
        if planted.exists():
            for path in sorted(planted.rglob("*"), reverse=True):
                path.rmdir() if path.is_dir() else path.unlink()
            planted.rmdir()


def test_sudo_works_outside_the_sandbox(sandboxed: Path) -> None:
    """Shows the sudo check above is meaningful on this host."""
    assert _run("sudo", "-n", "true").returncode == 0


def test_the_sandbox_keeps_host_shared_memory_out_but_lets_a_workload_use_its_own(run_sandboxed) -> None:
    host_file = Path("/dev/shm/pilot-sandbox-probe")
    host_file.write_text("host")
    try:
        assert not run_sandboxed(f"test -e {host_file}")
    finally:
        host_file.unlink()
    assert run_sandboxed("python3 -c 'import multiprocessing; multiprocessing.Lock()'")


def test_the_sandbox_resolves_hostnames(run_sandboxed) -> None:
    assert run_sandboxed("getent hosts github.com")


def test_the_sandbox_lets_frappe_create_its_global_lock_files(run_sandboxed, sandboxed: Path) -> None:
    """Frappe locks `<bench>/config/<name>.lock` with an exclusive create, so config/ must stay writable."""
    assert run_sandboxed(f"touch {sandboxed}/config/probe.lock && rm {sandboxed}/config/probe.lock")


def test_the_sandbox_lets_the_bench_write_its_runtime_files(run_sandboxed, sandboxed: Path) -> None:
    assert run_sandboxed(f"touch {sandboxed}/pids/probe && rm {sandboxed}/pids/probe")


def test_the_sandbox_lets_the_bench_write_its_own_files(run_sandboxed, sandboxed: Path) -> None:
    assert run_sandboxed(f"touch {sandboxed}/sites/sandbox-probe && rm {sandboxed}/sites/sandbox-probe")


def test_a_background_job_runs_in_the_sandboxed_worker(sandboxed: Path) -> None:
    backups = sandboxed / "sites" / SITE / "private" / "backups"
    before = set(backups.glob("*.sql.gz"))
    job = json.dumps({"method": "frappe.utils.backups.new_backup", "queue": "short"})
    result = _run(
        str(sandboxed / "env" / "bin" / "python"),
        "-m",
        "frappe.utils.bench_helper",
        "frappe",
        "--site",
        SITE,
        "execute",
        "frappe.enqueue",
        "--kwargs",
        job,
        cwd=sandboxed / "sites",
    )
    # `frappe execute` exits 1 when it cannot print the queued Job as JSON; the job is queued anyway.
    assert "Traceback" not in result.stderr, result.stderr
    for _ in range(60):
        if set(backups.glob("*.sql.gz")) - before:
            return
        time.sleep(2)
    pytest.fail("the worker did not write a backup")


def test_the_workload_still_reaches_the_database_socket(run_sandboxed, sandboxed: Path) -> None:
    socket = PilotSandboxPaths(Bench(sandboxed)).mariadb_socket_path
    assert run_sandboxed(f"python3 -c \"import socket; socket.socket(socket.AF_UNIX).connect('{socket}')\"")


def test_home_is_an_empty_tmpfs_in_the_web_process(sandboxed: Path) -> None:
    web = next(unit for unit in _workload_units(sandboxed) if unit.endswith("-web.service"))
    mountinfo = Path(f"/proc/{_systemctl_value(web, 'MainPID')}/mountinfo").read_text().splitlines()
    mounts = {line.split()[4]: line.split(" - ")[1].split()[0] for line in mountinfo}
    assert mounts.get("/home") == "tmpfs", mounts


def test_the_sandbox_keeps_the_tools_the_bench_runs_on(run_sandboxed, sandboxed: Path) -> None:
    assert run_sandboxed(f"{sandboxed}/env/bin/python -c 'import frappe'")
    assert run_sandboxed("node --version")


def test_a_declared_path_is_readable_and_the_rest_of_home_is_not(sandboxed: Path) -> None:
    shared = Path.home() / ".sandbox-probe-shared"
    shared.mkdir(exist_ok=True)
    (shared / "note.txt").write_text("shared")
    try:
        properties = [
            *SystemdProcessManager(Bench(sandboxed)).get_checked_sandbox_properties(),
            f"BindReadOnlyPaths={shared}",
        ]
        argv = ["systemd-run", "--user", "--wait", "--quiet", "--collect"]
        for directive in properties:
            argv += ["-p", directive]
        assert _run(*argv, "bash", "-c", f"cat {shared}/note.txt").returncode == 0
        assert _run(*argv, "bash", "-c", f"touch {shared}/x").returncode != 0
        assert _run(*argv, "bash", "-c", "test -e ~/.ssh").returncode != 0
    finally:
        (shared / "note.txt").unlink()
        shared.rmdir()
