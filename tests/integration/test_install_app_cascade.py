"""Integration test: installing helpdesk on a site auto-installs telephony.

`bench --site <site> install-app helpdesk` routes through pilot's own
InstallAppCommand -> Site.install_app -> a single subprocess call into the
bench's real `bench frappe --site <site> install-app helpdesk`. That's the
actual frappe.installer.install_app function (see
frappe/installer.py:install_app), which reads hooks.py's `required_apps`
and recursively calls itself for each one before installing the app —
telephony is declared there for helpdesk. No fixture apps are created here;
this uses helpdesk/telephony however they already exist on the target bench,
so it needs no network fetch and skips cleanly if they're not present.

Requires BENCH_TEST_ROOT (or the default test-bench) to already have
helpdesk and telephony cloned onto it — e.g. via `bench get-app helpdesk`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

HELPDESK = "helpdesk"
TELEPHONY = "telephony"


def _run(bench_bin: str, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([bench_bin, *args], cwd=cwd, capture_output=True, text=True)


def _installed_apps(bench_bin: str, bench_root: Path, site: str) -> list[str]:
    r = _run(bench_bin, "--site", site, "list-apps", cwd=bench_root)
    return [line.split()[0] for line in r.stdout.splitlines() if line.strip()]


def _uninstall_if_present(bench_bin: str, bench_root: Path, site: str, app: str) -> None:
    if app in _installed_apps(bench_bin, bench_root, site):
        subprocess.run(
            [bench_bin, "--site", site, "uninstall-app", app, "--yes", "--no-backup"],
            cwd=bench_root, capture_output=True,
        )


@pytest.mark.integration
def test_install_app_cascades_telephony_for_helpdesk(
    bench_root: Path, bench_bin: str, site_name: str
) -> None:
    if not (bench_root / "apps" / HELPDESK).is_dir() or not (bench_root / "apps" / TELEPHONY).is_dir():
        pytest.skip(
            f"{HELPDESK}/{TELEPHONY} not cloned on this bench — run "
            f"'bench get-app {HELPDESK}' first to exercise this cascade check."
        )

    # Both start uninstalled on the site — telephony must come back purely
    # from helpdesk's own required_apps cascade, not from us installing it.
    _uninstall_if_present(bench_bin, bench_root, site_name, HELPDESK)
    _uninstall_if_present(bench_bin, bench_root, site_name, TELEPHONY)

    result = _run(bench_bin, "--site", site_name, "install-app", HELPDESK, cwd=bench_root)
    assert result.returncode == 0, (
        f"install-app {HELPDESK} failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    installed = _installed_apps(bench_bin, bench_root, site_name)
    assert HELPDESK in installed, f"{HELPDESK} not installed on site.\nInstalled: {installed}"
    assert TELEPHONY in installed, (
        f"{TELEPHONY} (helpdesk's required_apps dependency) was not cascaded onto the site "
        f"by install-app.\nInstalled: {installed}"
    )
