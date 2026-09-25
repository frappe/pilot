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
ensure_tzdata() { echo ensure_tzdata; }
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
    assert "ensure_tzdata" in provisioned.stdout.splitlines()


def zoneinfo_dir(tmp_path: Path, *, with_alias: bool) -> Path:
    directory = tmp_path / "zoneinfo" / "Asia"
    directory.mkdir(parents=True, exist_ok=True)
    if with_alias:
        (directory / "Calcutta").write_text("")
    return directory.parent


@pytest.mark.parametrize("distro", ["ubuntu", "debian"])
def test_missing_timezone_aliases_are_installed(distro: str, tmp_path: Path) -> None:
    result = run_installer_functions(
        f"""
DISTRO={distro}
ZONEINFO_DIR={zoneinfo_dir(tmp_path, with_alias=False)}
pkg_install() {{ echo "pkg_install $*"; }}
ensure_tzdata_legacy
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "pkg_install tzdata-legacy" in result.stdout


def test_root_provisioning_installs_missing_timezone_aliases(tmp_path: Path) -> None:
    result = run_installer_functions(
        f"""
DISTRO=ubuntu
ZONEINFO_DIR={zoneinfo_dir(tmp_path, with_alias=False)}
is_root() {{ return 0; }}
ensure_curl() {{ return 0; }}
add_distro_repos() {{ return 0; }}
pkg_update() {{ return 0; }}
bootstrap_packages() {{ return 0; }}
install_database_engines() {{ return 0; }}
install_production_packages() {{ return 0; }}
disable_system_services() {{ return 0; }}
install_node() {{ return 0; }}
pkg_installed() {{ return 0; }}
pkg_install() {{ echo "pkg_install $*"; }}
install_system_packages
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "pkg_install tzdata-legacy" in result.stdout


def test_present_timezone_aliases_install_nothing(tmp_path: Path) -> None:
    result = run_installer_functions(
        f"""
DISTRO=ubuntu
ZONEINFO_DIR={zoneinfo_dir(tmp_path, with_alias=True)}
pkg_install() {{ echo "pkg_install $*"; }}
ensure_tzdata_legacy
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("distro", ["fedora", "arch"])
def test_distros_shipping_aliases_in_tzdata_install_nothing(distro: str, tmp_path: Path) -> None:
    result = run_installer_functions(
        f"""
DISTRO={distro}
ZONEINFO_DIR={zoneinfo_dir(tmp_path, with_alias=False)}
pkg_install() {{ echo "pkg_install $*"; }}
ensure_tzdata_legacy
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_unavailable_timezone_alias_package_does_not_abort_the_install(tmp_path: Path) -> None:
    result = run_installer_functions(
        f"""
set -e
DISTRO=ubuntu
ZONEINFO_DIR={zoneinfo_dir(tmp_path, with_alias=False)}
pkg_install() {{ return 1; }}
ensure_tzdata_legacy
echo reached_the_end
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "Warning: tzdata-legacy is unavailable" in result.stdout
    assert "reached_the_end" in result.stdout


def test_install_for_user_does_not_install_system_packages(tmp_path: Path) -> None:
    result = run_installer_functions(
        f"""
PILOT_DIR="{tmp_path}/pilot"
mkdir -p "$PILOT_DIR/bin"
touch "$PILOT_DIR/bin/pilot"
require_linger() {{ return 0; }}
fetch_pilot() {{ return 0; }}
ensure_uv() {{ return 0; }}
add_pilot_to_path() {{ return 0; }}
ensure_admin_venv() {{ return 0; }}
pkg_install() {{ echo "FAIL: pkg_install called"; exit 1; }}
ensure_tzdata() {{ echo "FAIL: ensure_tzdata called"; exit 1; }}
install_for_user
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "FAIL" not in result.stdout


# ── timezone_data_present ─────────────────────────────────────────────────────


def test_system_packages_present_passes_when_timezone_alias_missing(
    tmp_path: Path,
) -> None:
    """system_packages_present must not gate on timezone data.

    If it did, a bench-user rerun on a host provisioned before tzdata-legacy
    was added would fail the check, triggering install_system_packages with
    sudo — reintroducing the privilege-escalation bug.
    """
    result = run_installer_functions(
        f"""
DISTRO=ubuntu
ZONEINFO_DIR={zoneinfo_dir(tmp_path, with_alias=False)}
base_tools_present() {{ return 0; }}
pkg_installed() {{ return 0; }}
system_packages_present
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_install_for_user_warns_when_timezone_alias_missing(tmp_path: Path) -> None:
    """install_for_user prints an advisory to stderr when deprecated aliases are missing."""
    result = run_installer_functions(
        f"""
DISTRO=ubuntu
PILOT_DIR="{tmp_path}/pilot"
INSTALL_URL="https://example.com/install.sh"
ZONEINFO_DIR={zoneinfo_dir(tmp_path, with_alias=False)}
mkdir -p "$PILOT_DIR/bin"
touch "$PILOT_DIR/bin/pilot"
require_linger() {{ return 0; }}
fetch_pilot() {{ return 0; }}
ensure_uv() {{ return 0; }}
add_pilot_to_path() {{ return 0; }}
ensure_admin_venv() {{ return 0; }}
pkg_installed() {{ return 0; }}
install_for_user
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "Warning" in result.stderr
    assert "Asia/Calcutta" in result.stderr


def test_install_for_user_no_warning_when_timezone_alias_present(tmp_path: Path) -> None:
    result = run_installer_functions(
        f"""
DISTRO=ubuntu
PILOT_DIR="{tmp_path}/pilot"
ZONEINFO_DIR={zoneinfo_dir(tmp_path, with_alias=True)}
mkdir -p "$PILOT_DIR/bin"
touch "$PILOT_DIR/bin/pilot"
require_linger() {{ return 0; }}
fetch_pilot() {{ return 0; }}
ensure_uv() {{ return 0; }}
add_pilot_to_path() {{ return 0; }}
ensure_admin_venv() {{ return 0; }}
pkg_installed() {{ return 0; }}
install_for_user
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "Warning" not in result.stderr


@pytest.mark.parametrize("distro", ["ubuntu", "debian"])
def test_timezone_data_present_requires_legacy_alias(distro: str, tmp_path: Path) -> None:
    without_alias = run_installer_functions(
        f"""
DISTRO={distro}
ZONEINFO_DIR={zoneinfo_dir(tmp_path, with_alias=False)}
pkg_installed() {{ return 0; }}
timezone_data_present
""",
        tmp_path,
    )
    assert without_alias.returncode != 0

    with_alias = run_installer_functions(
        f"""
DISTRO={distro}
ZONEINFO_DIR={zoneinfo_dir(tmp_path, with_alias=True)}
pkg_installed() {{ return 0; }}
timezone_data_present
""",
        tmp_path,
    )
    assert with_alias.returncode == 0, with_alias.stderr


@pytest.mark.parametrize("distro", ["fedora", "arch"])
def test_timezone_data_present_skips_alias_check_on_fedora_and_arch(
    distro: str, tmp_path: Path
) -> None:
    result = run_installer_functions(
        f"""
DISTRO={distro}
ZONEINFO_DIR={zoneinfo_dir(tmp_path, with_alias=False)}
pkg_installed() {{ return 0; }}
timezone_data_present
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_timezone_data_present_fails_when_tzdata_not_installed(tmp_path: Path) -> None:
    result = run_installer_functions(
        """
DISTRO=ubuntu
ZONEINFO_DIR=/nonexistent
pkg_installed() { return 1; }
timezone_data_present
""",
        tmp_path,
    )
    assert result.returncode != 0
