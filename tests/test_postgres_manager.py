from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pilot.config.postgres_config import PostgresConfig
from pilot.managers.postgres_manager import (
    PostgresManager,
    pick_dedicated_postgres_port,
    supports_dedicated_postgres,
)

MODULE = "pilot.managers.postgres_manager"


def _mgr(**kwargs) -> PostgresManager:
    return PostgresManager(PostgresConfig(**kwargs))


def _dedicated(instance: str = "b1", **kwargs) -> PostgresManager:
    return PostgresManager(PostgresConfig(instance=instance, **kwargs))


# ── install ───────────────────────────────────────────────────────────────────


def test_is_installed_checks_binaries() -> None:
    with patch(f"{MODULE}.which", side_effect=lambda n: "/usr/bin/psql" if n == "psql" else None):
        assert _mgr().is_installed() is True
    with patch(f"{MODULE}.which", return_value=None):
        assert _mgr().is_installed() is False


def test_install_skips_when_present() -> None:
    m = _mgr()
    with patch.object(m, "is_installed", return_value=True), patch(f"{MODULE}.get_package_manager") as gpm:
        m.install()
    gpm.assert_not_called()


def test_install_uses_apt_packages_on_linux() -> None:
    m, pkg = _mgr(), MagicMock()
    with patch.object(m, "is_installed", return_value=False), patch(f"{MODULE}.is_macos", return_value=False), patch(f"{MODULE}.get_package_manager", return_value=pkg):
        m.install()
    pkg.install.assert_called_once_with("postgresql", "postgresql-client")


def test_install_uses_brew_formula_on_macos() -> None:
    m, pkg = _mgr(version="16"), MagicMock()
    with patch.object(m, "is_installed", return_value=False), patch(f"{MODULE}.is_macos", return_value=True), patch.object(m, "_installed_brew_formula", return_value=None), patch(f"{MODULE}.get_package_manager", return_value=pkg):
        m.install()
    pkg.install.assert_called_once_with("postgresql@16")


# ── secure ────────────────────────────────────────────────────────────────────


def test_secure_skips_when_no_password() -> None:
    m = _mgr(root_password="")
    with patch.object(m, "check_credentials") as cc, patch.object(m, "_run_sql_as_superuser") as run:
        m.secure()
    cc.assert_not_called()
    run.assert_not_called()


def test_secure_noop_when_credentials_already_work() -> None:
    m = _mgr(root_password="pw")
    with patch.object(m, "check_credentials", return_value=True), patch.object(m, "_run_sql_as_superuser") as run:
        m.secure()
    run.assert_not_called()


def test_secure_sets_password_then_verifies() -> None:
    m = _mgr(root_password="pw")
    with patch.object(m, "check_credentials", side_effect=[False, True]), patch.object(m, "_run_sql_as_superuser") as run:
        m.secure()
    run.assert_called_once()


def test_secure_raises_when_still_unauthenticated() -> None:
    m = _mgr(root_password="pw")
    with patch.object(m, "check_credentials", side_effect=[False, False]), patch.object(m, "_run_sql_as_superuser"):
        with pytest.raises(RuntimeError, match="authenticate"):
            m.secure()


def test_ensure_role_sql_creates_or_alters_with_quoting() -> None:
    sql = _mgr(admin_user="postgres", root_password="p'w")._ensure_role_sql()
    assert 'CREATE ROLE "postgres" WITH LOGIN SUPERUSER PASSWORD' in sql
    assert 'ALTER ROLE "postgres" WITH LOGIN SUPERUSER PASSWORD' in sql
    assert "'p''w'" in sql  # single quote doubled for the SQL literal


# ── credentials check ──────────────────────────────────────────────────────────


def test_check_credentials_uses_pgpassword_and_tcp() -> None:
    m = _mgr(root_password="pw", host="h", port=5440, admin_user="pg")
    captured: dict = {}

    def fake_run(cmd, env=None, capture_output=None, text=None):
        captured["cmd"], captured["env"] = cmd, env
        return MagicMock(returncode=0)

    with patch(f"{MODULE}.which", return_value="/usr/bin/psql"), patch(f"{MODULE}.subprocess.run", side_effect=fake_run):
        assert m.check_credentials() is True
    assert captured["env"]["PGPASSWORD"] == "pw"
    cmd = captured["cmd"]
    assert cmd[cmd.index("-U") + 1] == "pg"
    assert cmd[cmd.index("-p") + 1] == "5440"
    assert cmd[cmd.index("-h") + 1] == "h"


def test_check_credentials_false_without_psql() -> None:
    with patch(f"{MODULE}.which", return_value=None), patch(f"{MODULE}.is_macos", return_value=False):
        assert _mgr(root_password="pw").check_credentials() is False


# ── service control & provisioning ─────────────────────────────────────────────


def test_start_targets_systemctl_on_linux() -> None:
    m = _mgr()
    with patch(f"{MODULE}.is_macos", return_value=False), patch(f"{MODULE}.run_command") as rc:
        m.start()
    rc.assert_called_once_with(["sudo", "systemctl", "start", "postgresql"])


def test_start_targets_brew_on_macos() -> None:
    m = _mgr()
    with patch(f"{MODULE}.is_macos", return_value=True), patch.object(m, "_installed_brew_formula", return_value="postgresql@16"), patch(f"{MODULE}.run_command") as rc:
        m.start()
    rc.assert_called_once_with(["brew", "services", "start", "postgresql@16"])


def test_provision_orchestrates_steps() -> None:
    m = _mgr(root_password="pw")
    with patch(f"{MODULE}.is_alpine", return_value=False), patch.object(m, "install") as ins, patch.object(m, "enable") as en, patch.object(m, "is_running", return_value=False), patch.object(m, "start") as st, patch.object(m, "_wait_until_reachable"), patch.object(m, "secure") as sec:
        m.provision()
    ins.assert_called_once()
    en.assert_called_once()
    st.assert_called_once()
    sec.assert_called_once()


# ── Alpine package naming ───────────────────────────────────────────────────────


def test_install_uses_versioned_packages_on_alpine() -> None:
    m, pkg = _mgr(), MagicMock()
    with patch.object(m, "is_installed", return_value=False), patch(f"{MODULE}.is_macos", return_value=False), \
         patch(f"{MODULE}.is_alpine", return_value=True), patch.object(m, "_alpine_major", return_value="17"), \
         patch(f"{MODULE}.get_package_manager", return_value=pkg):
        m.install()
    pkg.install.assert_called_once_with("postgresql17", "postgresql17-client")


def test_alpine_dev_package_is_versioned() -> None:
    with patch.object(PostgresManager, "_alpine_major", return_value="17"):
        assert _mgr().alpine_dev_package() == "postgresql17-dev"


def test_alpine_major_prefers_configured_version() -> None:
    assert _mgr(version="16.2")._alpine_major() == "16"


# ── dedicated cluster ───────────────────────────────────────────────────────────


def test_is_dedicated() -> None:
    assert _mgr().is_dedicated is False
    assert _dedicated("b1").is_dedicated is True


def test_service_unit_shared_and_dedicated() -> None:
    assert _mgr().service_unit() == "postgresql"
    with patch.object(PostgresManager, "_cluster_version", return_value="16"):
        assert _dedicated("b1").service_unit() == "postgresql@16-b1"


def test_supports_dedicated_postgres_needs_systemd() -> None:
    with patch(f"{MODULE}.is_linux", return_value=True), patch(f"{MODULE}.is_alpine", return_value=False):
        assert supports_dedicated_postgres() is True
    with patch(f"{MODULE}.is_linux", return_value=True), patch(f"{MODULE}.is_alpine", return_value=True):
        assert supports_dedicated_postgres() is False
    with patch(f"{MODULE}.is_linux", return_value=False), patch(f"{MODULE}.is_alpine", return_value=False):
        assert supports_dedicated_postgres() is False


def test_pick_dedicated_postgres_port_skips_shared_and_siblings(tmp_path) -> None:
    siblings = [("a", SimpleNamespace(postgres=SimpleNamespace(instance="a", port=5433)))]
    with patch("pilot.utils.iter_sibling_benches", return_value=siblings), patch(f"{MODULE}._port_is_live", return_value=False):
        # 5432 (shared) and 5433 (sibling) are taken, so 5434 is next.
        assert pick_dedicated_postgres_port(tmp_path) == 5434


def test_provision_routes_to_instance_when_dedicated() -> None:
    m = _dedicated("b1", root_password="pw")
    with patch.object(m, "install"), patch.object(m, "_provision_instance") as prov, \
         patch.object(m, "_wait_until_reachable"), patch.object(m, "secure"):
        m.provision()
    prov.assert_called_once()


def test_provision_instance_creates_then_starts_cluster() -> None:
    m = _dedicated("b1", port=5440)
    with patch(f"{MODULE}.supports_dedicated_postgres", return_value=True), \
         patch.object(m, "_cluster_row", return_value=[]), patch.object(m, "_detected_version", return_value="16"), \
         patch.object(m, "is_running", return_value=False), patch.object(m, "start") as start, \
         patch.object(m, "enable"), patch(f"{MODULE}.run_command") as rc:
        m._provision_instance()
    args = rc.call_args[0][0]
    assert "pg_createcluster" in args and "16" in args and "b1" in args
    assert args[args.index("-p") + 1] == "5440"
    # Started directly afterwards, not via pg_createcluster --start (systemd).
    assert "--start" not in args
    start.assert_called_once()


def test_ctlcluster_skips_systemctl_redirect() -> None:
    m = _dedicated("b1")
    with patch.object(m, "_cluster_version", return_value="16"), patch(f"{MODULE}.run_command") as rc:
        m.start()
    args = rc.call_args[0][0]
    assert args[:2] == ["sudo", "pg_ctlcluster"]
    assert "--skip-systemctl-redirect" in args and args[-1] == "start"


def test_provision_instance_rejects_when_unsupported() -> None:
    m = _dedicated("b1")
    with patch(f"{MODULE}.supports_dedicated_postgres", return_value=False):
        with pytest.raises(RuntimeError, match="systemd"):
            m._provision_instance()


def test_remove_instance_drops_cluster() -> None:
    m = _dedicated("b1")
    with patch(f"{MODULE}.supports_dedicated_postgres", return_value=True), \
         patch.object(m, "_cluster_version", return_value="16"), patch(f"{MODULE}.run_command") as rc:
        m.remove_instance()
    args = rc.call_args[0][0]
    assert "pg_dropcluster" in args and "16" in args and "b1" in args and "--stop" in args


def test_remove_instance_noop_for_shared() -> None:
    with patch(f"{MODULE}.run_command") as rc:
        _mgr().remove_instance()
    rc.assert_not_called()


def test_run_sql_as_superuser_targets_cluster_port() -> None:
    m = _dedicated("b1", port=5440)
    with patch(f"{MODULE}.is_linux", return_value=True), patch(f"{MODULE}.subprocess.run") as run:
        m._run_sql_as_superuser("SELECT 1;")
    cmd = run.call_args[0][0]
    assert cmd[:4] == ["sudo", "-u", "postgres", "psql"]
    assert cmd[cmd.index("-p") + 1] == "5440"
