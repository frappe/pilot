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
        "nginx-mod-modsecurity",
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
pkg_available() {{ return 0; }}
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
enable_cron() { echo enable_cron; }
install_database_engines() { echo install_database_engines; }
install_production_packages() { echo install_production_packages; }
disable_system_services() { echo disable_system_services; }
install_node() { echo install_node; }
ensure_tzdata() { echo ensure_tzdata; }
install_system_packages
""",
        tmp_path,
    )
    assert provisioned.returncode == 0, provisioned.stderr
    assert "install_database_engines" in provisioned.stdout.splitlines()


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


def test_user_pass_never_installs_timezone_data(tmp_path: Path) -> None:
    """The bench user may have no sudo; the root pass installs tzdata."""
    result = run_installer_functions(
        """
require_linger() { :; }
fetch_pilot() { :; }
ensure_uv() { :; }
add_pilot_to_path() { :; }
ensure_admin_venv() { :; }
chmod() { :; }
PILOT_DIR=/nonexistent
pkg_install() { echo "pkg_install $*"; }
install_for_user
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "pkg_install" not in result.stdout


def test_missing_waf_module_is_skipped_with_a_warning(tmp_path: Path) -> None:
    """Ubuntu 22.04 does not package the ModSecurity module."""
    result = run_installer_functions(
        """
DISTRO=ubuntu
pkg_available() { return 1; }
pkg_install() { echo "pkg_install $*"; }
install_production_packages
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "WAF is unavailable" in result.stdout
    assert "pkg_install nginx certbot supervisor" in result.stdout
    assert "modsecurity" not in result.stdout.split("pkg_install", 1)[1]


def test_arch_keeps_an_installed_mariadb_provider(tmp_path: Path) -> None:
    """mariadb-lts provides mariadb; installing mariadb would conflict."""
    result = run_installer_functions(
        """
DISTRO=arch
pkg_installed() { case "$1" in mariadb*) return 0 ;; *) return 1 ;; esac; }
pkg_install() { echo "pkg_install $*"; }
install_database_engines
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "pkg_install postgresql postgresql-libs pkgconf redis"


def test_sudoers_grant_falls_back_when_wildcards_are_rejected(tmp_path: Path) -> None:
    """sudo-rs on Ubuntu 26.04 rejects wildcards in arguments."""
    result = run_installer_functions(
        """
visudo() { ! grep -q '\\*' "$2"; }
sudo() { echo "sudo-rs 0.2.13"; }
install() { cp "$3" "$TARGET"; }
TARGET=$(mktemp)
write_sudoers_file frappe-pilot-certbot "frappe ALL=(ALL) NOPASSWD: /usr/bin/test -f /a/*/b" "frappe ALL=(ALL) NOPASSWD: /usr/bin/test"
cat "$TARGET"
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "frappe ALL=(ALL) NOPASSWD: /usr/bin/test"
    assert "without argument limits" in result.stderr


def test_a_rerun_leaves_enabled_services_running(tmp_path: Path) -> None:
    """Production setup enables nginx; a root rerun must not take every bench down."""
    result = run_installer_functions(
        """
systemctl() { [ "$1" = "is-enabled" ] && [ "$2" = "nginx" ]; }
services_to_disable
""",
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "nginx" not in result.stdout.split()
    assert "mariadb" in result.stdout.split()


@pytest.mark.parametrize(("version", "is_accepted"), [("v18.20.3", False), ("v24.1.0", True), ("v26.0.0", True)])
def test_node_24_or_later_is_accepted(version: str, is_accepted: bool, tmp_path: Path) -> None:
    """Frappe needs Node 24 or later, and Arch and Homebrew install the current release."""
    result = run_installer_functions(
        f"""
DISTRO=ubuntu
node() {{ echo "{version}"; }}
install_node
echo reached_the_end
""",
        tmp_path,
    )
    assert ("reached_the_end" in result.stdout) is is_accepted
    assert ("Node.js 24 or later" in result.stdout) is not is_accepted


@pytest.mark.parametrize(
    ("os_release", "supported"),
    [
        ('ID=ubuntu\nVERSION_ID="22.04"\n', False),
        ('ID=ubuntu\nVERSION_ID="24.04"\n', True),
        ('ID=debian\nVERSION_ID="11"\n', False),
        ('ID=debian\nVERSION_ID="13"\n', True),
        ("ID=debian\n", True),  # testing and sid carry no VERSION_ID
        ('ID=fedora\nVERSION_ID="42"\n', False),
        ('ID=fedora\nVERSION_ID="44"\n', True),
        ('ID=linuxmint\nID_LIKE=ubuntu\nVERSION_ID="21.3"\n', True),
    ],
)
def test_only_the_last_two_releases_are_supported(os_release: str, supported: bool, tmp_path: Path) -> None:
    release_file = tmp_path / "os-release"
    release_file.write_text(os_release)

    result = run_installer_functions(f"require_supported_release {release_file}", tmp_path)

    assert (result.returncode == 0) is supported
