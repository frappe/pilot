"""Wizard-side PostgreSQL handling: password validation and dedicated-cluster
port assignment in admin/backend/views/setup.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from admin.backend.views.setup import _assign_postgres_port, _read_defaults, _validate

PORT_PICKER = "pilot.managers.postgres_manager.pick_dedicated_postgres_port"


# ── _validate ───────────────────────────────────────────────────────────────────


def test_validate_requires_postgres_password() -> None:
    error = _validate({"admin_password": "x", "db_type": "postgres", "postgres_password": ""})
    assert error and "postgres_password" in error


def test_validate_accepts_postgres_password() -> None:
    data = {"admin_password": "x", "db_type": "postgres", "postgres_password": "pw"}
    assert _validate(data) is None


def test_validate_requires_mariadb_password() -> None:
    error = _validate({"admin_password": "x", "db_type": "mariadb", "mariadb_password": ""})
    assert error and "mariadb_password" in error


# ── _assign_postgres_port ────────────────────────────────────────────────────────


def test_assign_port_shared_resets_to_default(tmp_path: Path) -> None:
    settings = {"db_type": "postgres", "postgres_instance": "", "postgres_port": 5444}
    _assign_postgres_port(tmp_path, settings)
    assert settings["postgres_port"] == 5432


def test_assign_port_dedicated_picks_free_port(tmp_path: Path) -> None:
    settings = {"db_type": "postgres", "postgres_instance": "b", "postgres_port": 5432}
    with patch(PORT_PICKER, return_value=5435):
        _assign_postgres_port(tmp_path, settings)
    assert settings["postgres_port"] == 5435


# ── _read_defaults ────────────────────────────────────────────────────────────


def test_read_defaults_omits_password_fallbacks_for_fresh_bench(tmp_path: Path) -> None:
    """No bench.toml yet — the wizard must not see the CLI's 'root' fallback as
    an already-chosen password, or it'll skip generating its own."""
    result = _read_defaults(tmp_path)
    assert "mariadb_password" not in result
    assert "postgres_password" not in result


def test_read_defaults_never_leaks_a_real_saved_password(tmp_path: Path) -> None:
    """Even a real, already-saved password must never come back over this
    endpoint — it's polled before login."""
    from admin.backend.views.setup import BenchTomlStore

    store = BenchTomlStore(tmp_path / "bench.toml")
    store.write_flat("bench6", {"mariadb_password": "s3cr3t", "postgres_password": "s3cr3t"})

    result = _read_defaults(tmp_path)
    assert "mariadb_password" not in result
    assert "postgres_password" not in result


def test_assign_port_keeps_existing_dedicated_port(tmp_path: Path) -> None:
    settings = {"db_type": "postgres", "postgres_instance": "b", "postgres_port": 5440}
    with patch(PORT_PICKER, return_value=5435):
        _assign_postgres_port(tmp_path, settings)
    assert settings["postgres_port"] == 5440  # idempotent — not re-picked


def test_assign_port_noop_for_mariadb(tmp_path: Path) -> None:
    settings = {"db_type": "mariadb", "postgres_port": 5432}
    _assign_postgres_port(tmp_path, settings)
    assert "postgres_port" in settings and settings["postgres_port"] == 5432
