from __future__ import annotations

import subprocess
from unittest.mock import patch

from bench_cli.config.mariadb_config import MariaDBConfig
from bench_cli.managers.mariadb_manager import MariaDBManager


def _manager(password: str = "root") -> MariaDBManager:
    return MariaDBManager(MariaDBConfig(root_password=password))


def _dedicated(instance: str = "b1", **kwargs) -> MariaDBManager:
    return MariaDBManager(MariaDBConfig(instance=instance, **kwargs))


# ── instance-aware helpers ────────────────────────────────────────────────────


def test_is_dedicated() -> None:
    assert _manager().is_dedicated is False
    assert _dedicated("b1").is_dedicated is True


def test_service_unit() -> None:
    assert _manager().service_unit() == "mariadb"
    assert _dedicated("b1").service_unit() == "mariadb@b1"


def test_instance_socket_defaults_to_per_instance_path() -> None:
    assert _dedicated("b1").instance_socket() == "/run/mysqld/mysqld-b1.sock"


def test_instance_socket_honors_explicit_socket_path() -> None:
    assert _dedicated("b1", socket_path="/tmp/custom.sock").instance_socket() == "/tmp/custom.sock"


def test_data_dir_defaults_to_sibling_path() -> None:
    # Sibling of /var/lib/mysql, never nested inside it (avoids clobbering a
    # legacy shared server's datadir).
    assert _dedicated("b1").data_dir() == "/var/lib/mysql-b1"


def test_data_dir_honors_explicit_value() -> None:
    assert _dedicated("b1", data_dir="/data/b1").data_dir() == "/data/b1"


def test_start_targets_instance_unit_when_dedicated() -> None:
    m = _dedicated("b1")
    with patch("bench_cli.managers.mariadb_manager.is_macos", return_value=False), patch(
        "bench_cli.managers.mariadb_manager.run_command"
    ) as rc:
        m.start()
    rc.assert_called_once_with(["sudo", "systemctl", "start", "mariadb@b1"])


def test_start_targets_shared_unit_when_legacy() -> None:
    m = _manager()
    with patch("bench_cli.managers.mariadb_manager.is_macos", return_value=False), patch(
        "bench_cli.managers.mariadb_manager.run_command"
    ) as rc:
        m.start()
    rc.assert_called_once_with(["sudo", "systemctl", "start", "mariadb"])


def test_run_sql_as_superuser_adds_socket_only_when_dedicated() -> None:
    with patch("bench_cli.managers.mariadb_manager.is_macos", return_value=False), patch(
        "bench_cli.managers.mariadb_manager.subprocess.run"
    ) as run:
        _dedicated("b1")._run_sql_as_superuser("SELECT 1;")
        dedicated_cmd = run.call_args[0][0]
        _manager()._run_sql_as_superuser("SELECT 1;")
        shared_cmd = run.call_args[0][0]
    assert dedicated_cmd == ["sudo", "mariadb", "--socket=/run/mysqld/mysqld-b1.sock"]
    assert shared_cmd == ["sudo", "mariadb"]


def test_provision_instance_starts_before_securing(tmp_path) -> None:
    m = _dedicated("b1")
    order: list = []

    def rec(name):
        return lambda *a, **k: order.append(name)

    with patch.object(m, "install", rec("install")), patch.object(
        m, "_write_instance_config", rec("write_config")
    ), patch.object(m, "_wait_until_reachable", rec("wait")), patch.object(
        m, "secure_installation", rec("secure")
    ), patch(
        "bench_cli.managers.mariadb_manager.run_command", lambda *a, **k: order.append(("rc", a[0]))
    ):
        m.provision_instance(tmp_path)

    enable_idx = next(i for i, c in enumerate(order) if isinstance(c, tuple) and "enable" in c[1])
    assert enable_idx < order.index("wait") < order.index("secure")


def test_provision_instance_rejects_legacy_bench(tmp_path) -> None:
    import pytest

    with pytest.raises(RuntimeError):
        _manager().provision_instance(tmp_path)


def test_write_instance_config_targets_mariadb_conf_d_with_pidfile(tmp_path) -> None:
    """Instance config lands in mariadb.conf.d/ (read after 50-server.cnf) with
    its own pid-file, so it isn't overridden by the base [mariadbd] group."""
    m = _dedicated("b1")
    with patch("bench_cli.managers.mariadb_manager.run_command") as rc:
        m._write_instance_config(tmp_path)

    content = (tmp_path / "mariadb" / "99-bench-b1.cnf").read_text()
    assert "[mariadbd.b1]" in content
    assert "datadir = /var/lib/mysql-b1" in content
    assert "socket = /run/mysqld/mysqld-b1.sock" in content
    assert "port = 3306" in content
    assert "pid-file = /run/mysqld/mysqld-b1.pid" in content

    dest = rc.call_args[0][0][-1]
    assert dest == "/etc/mysql/mariadb.conf.d/99-bench-b1.cnf"


def test_write_systemd_override_pins_escaped_group_suffix(tmp_path) -> None:
    """systemd's %I unescapes '-' to '/', so the packaged --defaults-group-suffix=.%I
    looks for [mariadbd.my/bench] and ignores our [mariadbd.my-bench] group. The
    per-instance drop-in pins %i (literal) so dashed bench names work."""
    m = _dedicated("my-bench")
    with patch("bench_cli.managers.mariadb_manager.run_command") as rc:
        m._write_systemd_override(tmp_path)

    content = (tmp_path / "mariadb" / "override-my-bench.conf").read_text()
    assert "--defaults-group-suffix=.%i" in content

    commands = [call.args[0] for call in rc.call_args_list]
    dest = next(c for c in commands if c[:2] == ["sudo", "cp"])[-1]
    assert dest == "/etc/systemd/system/mariadb@my-bench.service.d/override.conf"
    assert ["sudo", "systemctl", "daemon-reload"] in commands


# ── _sql_quote ────────────────────────────────────────────────────────────────


def test_sql_quote_plain() -> None:
    assert MariaDBManager._sql_quote("hunter2") == "'hunter2'"


def test_sql_quote_escapes_single_quote() -> None:
    assert MariaDBManager._sql_quote("a'b") == "'a\\'b'"


def test_sql_quote_escapes_backslash() -> None:
    assert MariaDBManager._sql_quote("a\\b") == "'a\\\\b'"


# ── secure_installation ───────────────────────────────────────────────────────


def test_secure_installation_noop_when_credentials_valid() -> None:
    manager = _manager()
    with patch.object(manager, "check_credentials", return_value=True), patch.object(
        manager, "_run_sql_as_superuser"
    ) as run_sql:
        manager.secure_installation()
    run_sql.assert_not_called()


def test_secure_installation_sets_password_and_hardens() -> None:
    manager = _manager("s3cret")
    with patch.object(manager, "check_credentials", return_value=False), patch.object(
        manager, "_run_sql_as_superuser"
    ) as run_sql:
        manager.secure_installation()
    run_sql.assert_called_once()
    sql = run_sql.call_args[0][0]
    assert "ALTER USER 'root'@'localhost' IDENTIFIED BY 's3cret';" in sql
    assert "DROP USER IF EXISTS ''@'localhost';" in sql
    assert "DROP DATABASE IF EXISTS test;" in sql
    assert "FLUSH PRIVILEGES;" in sql


# ── check_credentials ─────────────────────────────────────────────────────────


def test_check_credentials_true_on_successful_connect() -> None:
    manager = _manager()
    with patch("bench_cli.managers.mariadb_manager.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 0)
        assert manager.check_credentials("pw") is True
    # Password is passed via MYSQL_PWD env, never argv.
    args, kwargs = run.call_args
    assert "pw" not in args[0]
    assert kwargs["env"]["MYSQL_PWD"] == "pw"


def test_check_credentials_false_on_error() -> None:
    manager = _manager()
    with patch("bench_cli.managers.mariadb_manager.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 1)
        assert manager.check_credentials("wrong") is False


# ── /api/setup/validate-mariadb endpoint ──────────────────────────────────────


def _client(tmp_path):
    from admin.backend.app import create_app

    app = create_app(tmp_path)
    app.config["TESTING"] = True
    return app.test_client()


def _post_validate(client, password: str):
    return client.post("/api/setup/validate-mariadb", json={"mariadb_password": password})


def test_validate_endpoint_will_install_when_not_installed(tmp_path) -> None:
    with patch("bench_cli.managers.mariadb_manager.MariaDBManager.is_installed", return_value=False):
        resp = _post_validate(_client(tmp_path), "anything")
    assert resp.get_json() == {"state": "will_install"}


def test_validate_endpoint_valid(tmp_path) -> None:
    with patch("bench_cli.managers.mariadb_manager.MariaDBManager.is_installed", return_value=True), patch(
        "bench_cli.managers.mariadb_manager.MariaDBManager.check_credentials", return_value=True
    ):
        resp = _post_validate(_client(tmp_path), "correct")
    assert resp.get_json() == {"state": "valid"}


def test_validate_endpoint_invalid(tmp_path) -> None:
    with patch("bench_cli.managers.mariadb_manager.MariaDBManager.is_installed", return_value=True), patch(
        "bench_cli.managers.mariadb_manager.MariaDBManager.check_credentials", return_value=False
    ):
        resp = _post_validate(_client(tmp_path), "wrong")
    assert resp.get_json() == {"state": "invalid"}


def _write_dedicated_toml(tmp_path) -> None:
    from bench_cli.config.bench_toml_builder import BenchTomlBuilder

    settings = {"mariadb_instance": "b1", "mariadb_socket_path": "/run/mysqld/mysqld-b1.sock"}
    (tmp_path / "bench.toml").write_text(BenchTomlBuilder("b1", settings).render())


def test_validate_endpoint_dedicated_not_running_is_will_install(tmp_path) -> None:
    """A dedicated instance that init hasn't provisioned yet must not be treated
    as a wrong password — init will create and secure it."""
    _write_dedicated_toml(tmp_path)
    with patch("bench_cli.managers.mariadb_manager.MariaDBManager.is_installed", return_value=True), patch(
        "bench_cli.managers.mariadb_manager.MariaDBManager.check_credentials", return_value=False
    ), patch("bench_cli.managers.mariadb_manager.MariaDBManager.service_is_active", return_value=False):
        resp = _post_validate(_client(tmp_path), "wrong")
    assert resp.get_json() == {"state": "will_install"}


def test_validate_endpoint_dedicated_running_wrong_pw_is_invalid(tmp_path) -> None:
    _write_dedicated_toml(tmp_path)
    with patch("bench_cli.managers.mariadb_manager.MariaDBManager.is_installed", return_value=True), patch(
        "bench_cli.managers.mariadb_manager.MariaDBManager.check_credentials", return_value=False
    ), patch("bench_cli.managers.mariadb_manager.MariaDBManager.service_is_active", return_value=True):
        resp = _post_validate(_client(tmp_path), "wrong")
    assert resp.get_json() == {"state": "invalid"}
