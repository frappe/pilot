"""Wizard-side PostgreSQL handling: password validation in admin/backend/views/setup.py."""

from __future__ import annotations

from pathlib import Path

from admin.backend.api.v1.setup import read_defaults, validate_configuration


def test_validate_configuration_requires_postgres_password() -> None:
    error = validate_configuration({"admin_password": "x", "db_type": "postgres", "postgres_password": ""})
    assert error and "postgres_password" in error


def test_validate_configuration_accepts_postgres_password() -> None:
    data = {"admin_password": "x", "db_type": "postgres", "postgres_password": "pw"}
    assert validate_configuration(data) is None


def test_validate_configuration_requires_mariadb_password() -> None:
    error = validate_configuration({"admin_password": "x", "db_type": "mariadb", "mariadb_password": ""})
    assert error and "mariadb_password" in error


def test_validate_configuration_requires_host_for_existing_mariadb() -> None:
    data = {
        "admin_password": "x",
        "db_type": "mariadb",
        "mariadb_password": "pw",
        "mariadb_existing": True,
    }
    error = validate_configuration(data)
    assert error and "mariadb_host" in error


def test_validate_configuration_requires_admin_user_for_existing_mariadb() -> None:
    data = {
        "admin_password": "x",
        "db_type": "mariadb",
        "mariadb_password": "pw",
        "mariadb_existing": True,
        "mariadb_host": "db.example.com",
    }
    error = validate_configuration(data)
    assert error and "mariadb_admin_user" in error


def test_validate_configuration_accepts_complete_existing_mariadb() -> None:
    data = {
        "admin_password": "x",
        "db_type": "mariadb",
        "mariadb_password": "pw",
        "mariadb_existing": True,
        "mariadb_host": "db.example.com",
        "mariadb_admin_user": "admin",
    }
    assert validate_configuration(data) is None


def test_validate_configuration_ignores_host_when_not_marked_existing() -> None:
    data = {
        "admin_password": "x",
        "db_type": "mariadb",
        "mariadb_password": "pw",
        "mariadb_host": "db.example.com",
    }
    assert validate_configuration(data) is None


def test_read_defaults_omits_password_fallbacks_for_fresh_bench(tmp_path: Path) -> None:
    """No bench.toml yet - the wizard must not see the CLI's 'root' fallback as
    an already-chosen password, or it'll skip generating its own."""
    result = read_defaults(tmp_path)
    assert "mariadb_password" not in result
    assert "postgres_password" not in result
    assert result["admin_password_configured"] is False
    assert result["mariadb_password_configured"] is False
    assert result["postgres_password_configured"] is False


def test_read_defaults_flags_sibling_benches(tmp_path: Path) -> None:
    """The wizard prints `bench start` only while this is the sole bench."""
    bench_root = tmp_path / "one"
    bench_root.mkdir()
    (bench_root / "bench.toml").touch()
    assert read_defaults(bench_root)["is_only_bench"] is True

    sibling = tmp_path / "two"
    sibling.mkdir()
    (sibling / "bench.toml").touch()
    assert read_defaults(bench_root)["is_only_bench"] is False


def test_read_defaults_never_leaks_a_real_saved_password(tmp_path: Path) -> None:
    """Even a real, already-saved password must never come back over this
    endpoint - it's polled before login."""
    from admin.backend.api.v1.setup import BenchConfig

    BenchConfig.write_flat(
        tmp_path / "bench.toml", "bench6", {"mariadb_password": "s3cr3t", "postgres_password": "s3cr3t"}
    )

    result = read_defaults(tmp_path)
    assert "mariadb_password" not in result
    assert "postgres_password" not in result
    assert result["mariadb_password_configured"] is True
    assert result["postgres_password_configured"] is True
