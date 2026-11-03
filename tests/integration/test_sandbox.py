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
from pilot.core.bench import Bench
from pilot.managers.processes.systemd import SystemdProcessManager
from pilot.managers.systemd_user import systemctl_env

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
    assert str(sandboxed) in mounts, mounts


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
        f"cat ../{NEIGHBOUR}/secret.txt",
        "unshare -U true",
    ],
)
def test_the_sandbox_blocks(run_sandboxed, sandboxed: Path, line: str) -> None:
    line = line.replace("../", f"{sandboxed.parent}/")
    assert not run_sandboxed(line), f"sandbox allowed: {line}"


def test_sudo_works_outside_the_sandbox(sandboxed: Path) -> None:
    """Shows the sudo check above is meaningful on this host."""
    assert _run("sudo", "-n", "true").returncode == 0


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
    assert result.returncode == 0, result.stderr
    for _ in range(60):
        if set(backups.glob("*.sql.gz")) - before:
            return
        time.sleep(2)
    pytest.fail("the worker did not write a backup")
