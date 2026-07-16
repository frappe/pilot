"""
Integration tests for the app lifecycle: get-app → install-app → migrate →
remove-app → migrate-after-remove.

Each test in TestAppLifecycle relies on the previous one having succeeded, so
run the class as a whole.  They exercise the bench-cli CLI passthrough, which
forwards unrecognised commands directly to the Frappe bench binary.

Prerequisites (once per developer machine):
    cd test-bench && bench init

In CI the BENCH_TEST_ROOT env var points at the already-initialised bench that
the earlier workflow steps set up.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

APP_NAME = "testapp"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(bench_bin: str, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [bench_bin, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _installed_apps(bench_bin: str, bench_root: Path, site: str) -> list[str]:
    """Return app names installed on *site*. list-apps output is '<name> <version> <branch>'."""
    r = _run(bench_bin, "--site", site, "list-apps", cwd=bench_root)
    return [line.split()[0] for line in r.stdout.splitlines() if line.strip()]


def _app_in_venv(bench_root: Path) -> bool:
    """Check that testapp is importable from the bench virtualenv."""
    python = bench_root / "env" / "bin" / "python"
    r = subprocess.run(
        [str(python), "-c", f"import {APP_NAME}"],
        capture_output=True,
    )
    return r.returncode == 0


# ---------------------------------------------------------------------------
# Module-level cleanup: remove testapp from the bench before and after the
# tests so re-runs start from a clean state.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def clean_slate(bench_root: Path, bench_bin: str, site_name: str):
    """Ensure testapp is absent before and after the test module runs."""
    _purge_testapp(bench_root, bench_bin, site_name)
    yield
    _purge_testapp(bench_root, bench_bin, site_name)


def _purge_testapp(bench_root: Path, bench_bin: str, site: str) -> None:
    # Remove from site if installed
    installed = _installed_apps(bench_bin, bench_root, site)
    if APP_NAME in installed:
        subprocess.run(
            [bench_bin, "--site", site, "uninstall-app", APP_NAME, "--yes", "--no-backup"],
            cwd=bench_root,
            capture_output=True,
        )

    # Remove from apps/ directory
    app_path = bench_root / "apps" / APP_NAME
    if app_path.exists():
        shutil.rmtree(app_path)

    # Remove from sites/apps.txt
    apps_txt = bench_root / "sites" / "apps.txt"
    if apps_txt.exists():
        lines = [line for line in apps_txt.read_text().splitlines() if line.strip() != APP_NAME]
        apps_txt.write_text("\n".join(lines) + ("\n" if lines else ""))


# ---------------------------------------------------------------------------
# Tests (run in definition order)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestAppLifecycle:

    def test_get_app_clones_and_installs(
        self, bench_root: Path, bench_bin: str, testapp_repo: Path
    ) -> None:
        """
        bench get-app <local-repo>
        → passthrough → env/bin/bench get-app <local-repo>
        Clones the repo into apps/testapp and pip-installs it editable.
        """
        result = _run(bench_bin, "get-app", str(testapp_repo), cwd=bench_root)
        assert result.returncode == 0, (
            f"get-app failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        assert (bench_root / "apps" / APP_NAME).is_dir(), (
            f"apps/{APP_NAME} directory not created after get-app"
        )
        assert _app_in_venv(bench_root), (
            f"{APP_NAME} not importable from bench virtualenv after get-app"
        )

    def test_install_app_on_site(
        self, bench_root: Path, bench_bin: str, site_name: str
    ) -> None:
        """
        bench --site site1.localhost install-app testapp
        → passthrough → env/bin/bench --site site1.localhost install-app testapp
        Registers the app on the site.
        """
        if not (bench_root / "apps" / APP_NAME).is_dir():
            pytest.skip(f"{APP_NAME} not in apps/ — run test_get_app first")

        result = _run(
            bench_bin, "--site", site_name, "install-app", APP_NAME,
            cwd=bench_root,
        )
        assert result.returncode == 0, (
            f"install-app failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        installed = _installed_apps(bench_bin, bench_root, site_name)
        assert APP_NAME in installed, (
            f"{APP_NAME} not in list-apps output after install-app.\n"
            f"Installed: {installed}"
        )

    def test_migrate_after_install(
        self, bench_root: Path, bench_bin: str, site_name: str
    ) -> None:
        """
        bench --site site1.localhost migrate
        → passthrough → env/bin/bench --site site1.localhost migrate
        Runs DB migrations; must succeed with testapp installed.
        """
        result = _run(bench_bin, "--site", site_name, "migrate", cwd=bench_root)
        assert result.returncode == 0, (
            f"migrate failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_remove_app_from_site(
        self, bench_root: Path, bench_bin: str, site_name: str
    ) -> None:
        """
        bench --site site1.localhost uninstall-app testapp --yes --no-backup
        → passthrough → env/bin/bench frappe --site site1.localhost uninstall-app testapp --yes --no-backup
        Unregisters the app from the site.
        """
        installed = _installed_apps(bench_bin, bench_root, site_name)
        if APP_NAME not in installed:
            pytest.skip(f"{APP_NAME} not installed on {site_name} — run test_install_app first")

        result = _run(
            bench_bin, "--site", site_name, "uninstall-app", APP_NAME, "--yes", "--no-backup",
            cwd=bench_root,
        )
        assert result.returncode == 0, (
            f"uninstall-app failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        installed_after = _installed_apps(bench_bin, bench_root, site_name)
        assert APP_NAME not in installed_after, (
            f"{APP_NAME} still in list-apps after remove-app.\n"
            f"Installed: {installed_after}"
        )

    def test_migrate_after_remove(
        self, bench_root: Path, bench_bin: str, site_name: str
    ) -> None:
        """
        bench --site site1.localhost migrate
        Verifies the site is still healthy after app removal.
        """
        result = _run(bench_bin, "--site", site_name, "migrate", cwd=bench_root)
        assert result.returncode == 0, (
            f"migrate after remove-app failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
