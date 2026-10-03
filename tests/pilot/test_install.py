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
        "cron",
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
        "cron",
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
        "cronie",
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
        "cronie",
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
    # Bench-user second pass: all packages present → return immediately without
    # touching system services (enable_cron_service requires root).
    skipped = run_installer_functions(
        """
DISTRO=ubuntu
is_root() { return 1; }
system_packages_present() { return 0; }
enable_cron_service() { echo enable_cron_service; }
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
enable_cron_service() { echo enable_cron_service; }
install_node() { echo install_node; }
install_system_packages
""",
        tmp_path,
    )
    assert provisioned.returncode == 0, provisioned.stderr
    assert "install_database_engines" in provisioned.stdout.splitlines()
    assert "enable_cron_service" in provisioned.stdout.splitlines()


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


@pytest.mark.parametrize(
    ("distro", "expected_service"),
    [
        ("arch", "cronie"),
        ("fedora", "crond"),
        ("debian", "cron"),
        ("ubuntu", "cron"),
    ],
)
def test_enable_cron_service_starts_distro_daemon(
    distro: str, expected_service: str, tmp_path: Path
) -> None:
    # Cron not running at all → must enable it.
    result = run_installer_functions(
        f"""
DISTRO={distro}
systemd_booted() {{ return 0; }}
systemctl() {{ return 1; }}
run_sudo() {{ echo "run_sudo $*"; }}
enable_cron_service
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"run_sudo systemctl enable --now {expected_service}"


def test_enable_cron_service_starts_when_active_but_disabled(tmp_path: Path) -> None:
    # Running now but not enabled at boot → must still call enable --now so it
    # survives a reboot.
    result = run_installer_functions(
        """
DISTRO=ubuntu
systemd_booted() { return 0; }
systemctl() { [ "$1" = "is-active" ] && return 0; return 1; }
run_sudo() { echo "run_sudo $*"; }
enable_cron_service
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "run_sudo systemctl enable --now cron"


def test_enable_cron_service_skips_when_active_and_enabled(tmp_path: Path) -> None:
    result = run_installer_functions(
        """
DISTRO=ubuntu
systemd_booted() { return 0; }
systemctl() { return 0; }
run_sudo() { echo "run_sudo $*"; }
enable_cron_service
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_enable_cron_service_skips_when_systemd_not_booted(tmp_path: Path) -> None:
    result = run_installer_functions(
        """
DISTRO=ubuntu
systemd_booted() { return 1; }
run_sudo() { echo "run_sudo $*"; }
enable_cron_service
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("distro", ["macos", "unknown"])
def test_enable_cron_service_skips_unsupported_distros(
    distro: str, tmp_path: Path
) -> None:
    result = run_installer_functions(
        f"""
DISTRO={distro}
systemd_booted() {{ return 0; }}
run_sudo() {{ echo "run_sudo $*"; }}
enable_cron_service
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_enable_cron_service_surfaces_systemctl_failure(tmp_path: Path) -> None:
    result = run_installer_functions(
        """
set -e
DISTRO=ubuntu
systemd_booted() { return 0; }
systemctl() { return 1; }
run_sudo() { echo "systemctl: unit failed to start" >&2; return 1; }
enable_cron_service
""",
        tmp_path,
    )
    assert result.returncode != 0
    assert "systemctl: unit failed to start" in result.stderr
