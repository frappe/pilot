from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pilot.config.postgres_config import PostgresConfig
from pilot.managers.postgres_manager import PostgresManager

MODULE = "pilot.managers.postgres_manager"
BASE_MODULE = "pilot.managers.user_owned_db_manager"


def _mgr(**kwargs) -> PostgresManager:
    return PostgresManager(PostgresConfig(**kwargs))


# ── install ───────────────────────────────────────────────────────────────────


def test_is_installed_checks_binaries() -> None:
    with patch(f"{MODULE}.which", side_effect=lambda n: "/usr/bin/psql" if n == "psql" else None):
        assert _mgr().is_installed() is True
    with patch(f"{MODULE}.which", return_value=None):
        assert _mgr().is_installed() is False


def test_install_skips_when_present() -> None:
    m = _mgr()
    with patch.object(m, "is_installed", return_value=True), patch(f"{BASE_MODULE}.get_package_manager") as gpm:
        m.install()
    gpm.assert_not_called()


def test_install_raises_when_missing_on_linux() -> None:
    m = _mgr()
    with patch.object(m, "is_installed", return_value=False), \
         patch(f"{BASE_MODULE}.is_macos", return_value=False):
        with pytest.raises(RuntimeError, match="install.sh"):
            m.install()


def test_install_uses_brew_formula_on_macos() -> None:
    m, pkg = _mgr(), MagicMock()
    with patch.object(m, "is_installed", return_value=False), patch(f"{BASE_MODULE}.is_macos", return_value=True), \
         patch.object(m, "_installed_brew_formula", return_value=None), patch(f"{BASE_MODULE}.get_package_manager", return_value=pkg):
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


# ── service control ─────────────────────────────────────────────────────────────


def test_start_targets_systemctl_user_on_linux() -> None:
    m = _mgr()
    with patch(f"{BASE_MODULE}.is_macos", return_value=False), \
         patch(f"{BASE_MODULE}.run_command") as rc:
        m.start()
    assert rc.call_args.args[0] == ["systemctl", "--user", "start", "pilot-postgres.service"]


def test_start_targets_brew_on_macos() -> None:
    m = _mgr()
    with patch(f"{BASE_MODULE}.is_macos", return_value=True), patch.object(m, "_installed_brew_formula", return_value="postgresql@16"), patch(f"{BASE_MODULE}.run_command") as rc:
        m.start()
    rc.assert_called_once_with(["brew", "services", "start", "postgresql@16"])


# ── provisioning ──────────────────────────────────────────────────────────────


def test_provision_orchestrates_steps_on_linux() -> None:
    m = _mgr(root_password="pw")
    with patch(f"{MODULE}.is_macos", return_value=False), \
         patch.object(m, "install") as ins, patch.object(m, "_provision_user_owned") as prov, \
         patch.object(m, "_wait_until_reachable"), patch.object(m, "secure") as sec:
        m.provision()
    ins.assert_called_once()
    prov.assert_called_once()
    sec.assert_called_once()


def test_is_provisioned_on_macos_uses_marker_not_unit_file(tmp_path) -> None:
    """No systemd --user unit ever exists on macOS — is_provisioned() must not
    always read False there, or a second bench's wizard validation would
    think the server is fresh and silently reset an already-secured password."""
    m = _mgr()
    with patch(f"{MODULE}.is_macos", return_value=True), \
         patch.object(m, "_macos_provisioned_marker", return_value=tmp_path / ".provisioned"):
        assert m.is_provisioned() is False
        (tmp_path / ".provisioned").touch()
        assert m.is_provisioned() is True


def test_provision_macos_writes_marker_after_securing(tmp_path) -> None:
    m = _mgr(root_password="pw")
    marker = tmp_path / "state" / ".provisioned"
    with patch(f"{MODULE}.is_macos", return_value=True), \
         patch.object(m, "install"), patch.object(m, "is_running", return_value=True), \
         patch.object(m, "_wait_until_reachable"), patch.object(m, "secure") as sec, \
         patch.object(m, "_macos_provisioned_marker", return_value=marker):
        m.provision()
    sec.assert_called_once()
    assert marker.exists()


def test_provision_user_owned_initialises_and_installs_unit_when_fresh(tmp_path) -> None:
    m = _mgr(port=5440)
    with patch.object(m, "data_dir", return_value=tmp_path / "data"), \
         patch.object(m, "is_provisioned", return_value=False), \
         patch.object(m, "_ensure_port_available"), \
         patch.object(m, "is_running", return_value=False), \
         patch.object(m, "_install_unit") as install_unit, \
         patch(f"{MODULE}.run_command") as rc:
        m._provision_user_owned()
    install_unit.assert_called_once()
    argv_calls = [c.args[0] for c in rc.call_args_list]
    assert any("initdb" in argv for argv in argv_calls)
    initdb_call = next(argv for argv in argv_calls if "initdb" in argv)
    assert "-D" in initdb_call
    assert "--username" not in initdb_call  # bootstrap superuser = current OS user


def test_ensure_port_available_raises_when_port_taken() -> None:
    import socket as socket_module

    m = _mgr()
    with socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        m.config.port = srv.getsockname()[1]
        with pytest.raises(RuntimeError, match="already in use"):
            m._ensure_port_available()


def test_ensure_port_available_passes_when_free() -> None:
    m = _mgr(port=65532)
    m._ensure_port_available()  # no raise


def test_provision_user_owned_reuses_already_provisioned_server() -> None:
    m = _mgr()
    with patch.object(m, "is_provisioned", return_value=True), \
         patch.object(m, "is_running", return_value=True), \
         patch.object(m, "_install_unit") as install_unit, \
         patch(f"{MODULE}.run_command") as rc:
        m._provision_user_owned()
    install_unit.assert_not_called()
    rc.assert_not_called()


def test_run_sql_as_superuser_uses_local_psql() -> None:
    m = _mgr(port=5440)
    with patch.object(m, "_psql", return_value="/usr/bin/psql"), \
         patch(f"{MODULE}.subprocess.run") as run:
        m._run_sql_as_superuser("SELECT 1;")
    cmd = run.call_args[0][0]
    assert cmd[0] == "/usr/bin/psql"
    assert "sudo" not in cmd
    assert cmd[cmd.index("-p") + 1] == "5440"


def test_run_sql_as_superuser_targets_own_socket_dir_on_linux() -> None:
    m = _mgr(port=5440)
    with patch.object(m, "_psql", return_value="/usr/bin/psql"), \
         patch(f"{MODULE}.is_macos", return_value=False), \
         patch(f"{MODULE}.subprocess.run") as run:
        m._run_sql_as_superuser("SELECT 1;")
    cmd = run.call_args[0][0]
    assert cmd[cmd.index("-h") + 1] == str(m.socket_dir())


def test_run_sql_as_superuser_uses_default_socket_on_macos() -> None:
    m = _mgr(port=5440)
    with patch.object(m, "_psql", return_value="/usr/bin/psql"), \
         patch(f"{MODULE}.is_macos", return_value=True), \
         patch(f"{MODULE}.subprocess.run") as run:
        m._run_sql_as_superuser("SELECT 1;")
    cmd = run.call_args[0][0]
    assert "-h" not in cmd


def test_install_unit_pins_unix_socket_directories_to_owned_dir(tmp_path) -> None:
    m = _mgr(port=5440)
    with patch.object(m, "unit_path", return_value=tmp_path / "pilot-postgres.service"), \
         patch.object(m, "_user_unit_dir", return_value=tmp_path), \
         patch(f"{MODULE}.which", return_value="/usr/lib/postgresql/bin/postgres"), \
         patch(f"{MODULE}.run_command"):
        m._install_unit()
    content = (tmp_path / "pilot-postgres.service").read_text()
    assert f"unix_socket_directories={m.socket_dir()}" in content


def test_provision_user_owned_creates_socket_dir(tmp_path) -> None:
    m = _mgr(port=5440)
    with patch.object(m, "data_dir", return_value=tmp_path / "data"), \
         patch.object(m, "socket_dir", return_value=tmp_path / "run"), \
         patch.object(m, "is_provisioned", return_value=False), \
         patch.object(m, "_ensure_port_available"), \
         patch.object(m, "is_running", return_value=False), \
         patch.object(m, "_install_unit"), \
         patch(f"{MODULE}.run_command"):
        m._provision_user_owned()
    assert (tmp_path / "run").is_dir()
