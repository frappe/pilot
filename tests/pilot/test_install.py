from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


INSTALLER = Path(__file__).parents[2] / "install.sh"
INSTALLER_FUNCTIONS = INSTALLER.read_text().split(
    "# ── run ───────────────────────────────────────────────────────────────────────"
)[0]

EXPECTED_PACKAGES = {
    "macos": [
        "mariadb@11.8",
        "postgresql@16",
        "redis",
        "nginx",
        "certbot",
    ],
    "debian": [
        "mariadb-server",
        "mariadb-client",
        "libmariadb-dev",
        "postgresql",
        "postgresql-client",
        "libpq-dev",
        "pkg-config",
        "redis-server",
        "nginx",
        "certbot",
        "supervisor",
        "libnginx-mod-http-modsecurity",
    ],
    "ubuntu": [
        "mariadb-server",
        "mariadb-client",
        "libmariadb-dev",
        "postgresql",
        "postgresql-client",
        "libpq-dev",
        "pkg-config",
        "redis-server",
        "nginx",
        "certbot",
        "supervisor",
        "libnginx-mod-http-modsecurity",
    ],
    "fedora": [
        "mariadb-server",
        "mariadb",
        "mariadb-connector-c-devel",
        "postgresql-server",
        "postgresql",
        "libpq-devel",
        "pkgconf-pkg-config",
        "valkey",
        "nginx",
        "certbot",
        "supervisor",
    ],
    "arch": [
        "mariadb",
        "mariadb-clients",
        "mariadb-libs",
        "postgresql",
        "postgresql-libs",
        "pkgconf",
        "redis",
        "nginx",
        "certbot",
        "supervisor",
    ],
}


def run_installer_functions(body: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for executable in ("node", "sudo"):
        path = bin_dir / executable
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return subprocess.run(
        ["sh", "-c", f"{INSTALLER_FUNCTIONS}\n{body}"],
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )


@pytest.mark.parametrize(("distro", "expected"), EXPECTED_PACKAGES.items())
def test_system_packages_present_checks_distro_packages(
    distro: str, expected: list[str], tmp_path: Path
) -> None:
    result = run_installer_functions(
        f"""
DISTRO={distro}
base_tools_present() {{ return 0; }}
pkg_installed() {{ printf '%s\\n' "$1"; return 0; }}
system_packages_present
""",
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == expected


@pytest.mark.parametrize("distro", EXPECTED_PACKAGES)
def test_system_packages_present_fails_when_required_package_is_missing(
    distro: str, tmp_path: Path
) -> None:
    missing = EXPECTED_PACKAGES[distro][0]
    result = run_installer_functions(
        f"""
DISTRO={distro}
base_tools_present() {{ return 0; }}
pkg_installed() {{ [ "$1" != "{missing}" ]; }}
system_packages_present
""",
        tmp_path,
    )

    assert result.returncode != 0


def test_non_root_install_skips_provisioning_only_when_stack_is_present(
    tmp_path: Path,
) -> None:
    skipped = run_installer_functions(
        """
DISTRO=ubuntu
is_root() { return 1; }
system_packages_present() { return 0; }
ensure_curl() { echo ensure_curl; }
install_system_packages
""",
        tmp_path,
    )
    assert skipped.returncode == 0, skipped.stderr
    assert skipped.stdout == ""

    provisioned = run_installer_functions(
        """
DISTRO=ubuntu
is_root() { return 1; }
system_packages_present() { return 1; }
ensure_curl() { echo ensure_curl; }
add_distro_repos() { echo add_distro_repos; }
pkg_update() { echo pkg_update; }
bootstrap_packages() { echo bootstrap_packages; }
install_database_engines() { echo install_database_engines; }
install_production_packages() { echo install_production_packages; }
disable_system_services() { echo disable_system_services; }
install_node() { echo install_node; }
install_system_packages
""",
        tmp_path,
    )
    assert provisioned.returncode == 0, provisioned.stderr
    assert "install_database_engines" in provisioned.stdout.splitlines()
