"""Tests for BenchConfig's file-store surface: read/write/edit of bench.toml."""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

import pytest

from pilot.config import BenchConfig
from pilot.exceptions import ConfigError
from pilot.internal.atomic_file import exclusive_file_lock


def _write_bench(bench_dir: Path, name: str = "test") -> Path:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(BenchConfig.from_flat(name).dumps())
    return bench_dir


def test_toml_path_resolves_bench_dir(tmp_path: Path) -> None:
    assert BenchConfig.toml_path(tmp_path) == tmp_path / "bench.toml"


def test_toml_path_accepts_directory_or_file(tmp_path: Path) -> None:
    (tmp_path / "bench.toml").write_text(BenchConfig.from_flat("x").dumps())
    assert BenchConfig.toml_path(tmp_path) == BenchConfig.toml_path(tmp_path / "bench.toml")


def test_exists_reflects_file(tmp_path: Path) -> None:
    assert not BenchConfig.exists(tmp_path)
    _write_bench(tmp_path)
    assert BenchConfig.exists(tmp_path)


def test_read_returns_validated_config(tmp_path: Path) -> None:
    bench_root = _write_bench(tmp_path, "mybench")
    assert BenchConfig.read(bench_root).name == "mybench"


def test_read_no_validate_allows_half_configured(tmp_path: Path) -> None:
    (tmp_path / "bench.toml").write_text('[bench]\nname = "half"\n')
    with pytest.raises(ConfigError):
        BenchConfig.read(tmp_path)
    assert BenchConfig.read(tmp_path, validate=False).name == "half"


def test_read_raw_preserves_unmodeled_sections(tmp_path: Path) -> None:
    bench_root = _write_bench(tmp_path)
    raw = BenchConfig.read_raw(bench_root)
    raw["sites"] = [{"name": "site1"}]
    BenchConfig.write_raw(bench_root, raw)
    assert BenchConfig.read_raw(bench_root)["sites"] == [{"name": "site1"}]


def test_read_flat_matches_from_flat(tmp_path: Path) -> None:
    bench_root = _write_bench(tmp_path, "flatbench")
    assert BenchConfig.read_flat(bench_root)["bench_name"] == "flatbench"


def test_write_round_trips_config(tmp_path: Path) -> None:
    bench_root = _write_bench(tmp_path, "rt")
    config = BenchConfig.read(bench_root)
    config.http_port = 8123
    config.write(bench_root)
    assert BenchConfig.read(bench_root).http_port == 8123


def test_write_rejects_invalid_config_without_replacing_file(tmp_path: Path) -> None:
    bench_root = _write_bench(tmp_path, "valid")
    toml_path = BenchConfig.toml_path(bench_root)
    original = toml_path.read_bytes()
    config = BenchConfig.read(bench_root)
    config.http_port = 0

    with pytest.raises(ConfigError):
        config.write(bench_root)

    assert toml_path.read_bytes() == original


def test_write_raw_validates_known_config_without_dropping_custom_keys(tmp_path: Path) -> None:
    bench_root = _write_bench(tmp_path, "valid")
    toml_path = BenchConfig.toml_path(bench_root)
    raw = BenchConfig.read_raw(bench_root)
    raw["custom_plugin"] = {"operator_key": "kept"}
    BenchConfig.write_raw(bench_root, raw)

    assert BenchConfig.read_raw(bench_root)["custom_plugin"] == {"operator_key": "kept"}

    original = toml_path.read_bytes()
    raw["bench"]["http_port"] = 0
    with pytest.raises(ConfigError):
        BenchConfig.write_raw(bench_root, raw)
    assert toml_path.read_bytes() == original


def test_failed_atomic_replace_keeps_existing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bench_root = _write_bench(tmp_path, "stable")
    toml_path = BenchConfig.toml_path(bench_root)
    original = toml_path.read_bytes()
    config = BenchConfig.read(bench_root)
    config.http_port = 8123

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr("pilot.internal.atomic_file.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        config.write(bench_root)

    assert toml_path.read_bytes() == original
    assert not list(tmp_path.glob(".bench.toml.*.tmp"))


def test_write_fsyncs_file_and_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bench_root = _write_bench(tmp_path, "durable")
    config = BenchConfig.read(bench_root)
    events = []
    real_replace = os.replace

    def replace(source, destination):
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr("pilot.internal.atomic_file.os.fsync", lambda descriptor: events.append("fsync"))
    monkeypatch.setattr("pilot.internal.atomic_file.os.replace", replace)

    config.write(bench_root)

    assert events == ["fsync", "replace", "fsync"]


def test_concurrent_raw_edits_preserve_both_updates(tmp_path: Path) -> None:
    bench_root = _write_bench(tmp_path, "locked")
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first_edit() -> None:
        with BenchConfig.open(bench_root, mode="raw") as raw:
            raw.setdefault("custom", {})["first"] = True
            first_entered.set()
            assert release_first.wait(timeout=1)

    def second_edit() -> None:
        assert first_entered.wait(timeout=1)
        with BenchConfig.open(bench_root, mode="raw") as raw:
            second_entered.set()
            raw.setdefault("custom", {})["second"] = True

    first_writer = threading.Thread(target=first_edit)
    second_writer = threading.Thread(target=second_edit)
    first_writer.start()
    second_writer.start()
    assert first_entered.wait(timeout=1)
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    first_writer.join(timeout=1)
    second_writer.join(timeout=1)

    assert BenchConfig.read_raw(bench_root)["custom"] == {"first": True, "second": True}
    lock_metadata = (tmp_path / ".bench.toml.lock").stat()
    config_metadata = BenchConfig.toml_path(bench_root).stat()
    assert stat.S_IMODE(lock_metadata.st_mode) == 0o600
    assert lock_metadata.st_uid == config_metadata.st_uid


def test_nonblocking_lock_fails_when_another_thread_holds_it(tmp_path: Path) -> None:
    target = tmp_path / "operation"

    with (
        exclusive_file_lock(target),
        pytest.raises(BlockingIOError),
        exclusive_file_lock(target, blocking=False),
    ):
        pass


def test_unchanged_transaction_does_not_replace_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bench_root = _write_bench(tmp_path, "unchanged")

    def fail_replace(source, destination):
        raise AssertionError("unchanged transaction replaced the file")

    monkeypatch.setattr("pilot.internal.atomic_file.os.replace", fail_replace)

    with BenchConfig.open(bench_root, mode="raw"):
        pass


def test_atomic_write_refuses_symbolic_link_destination(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config = BenchConfig.read(_write_bench(config_dir, "safe"))
    target = tmp_path / "target.toml"
    target.write_text("untouched")
    link = tmp_path / "bench.toml"
    link.symlink_to(target)

    with pytest.raises(OSError, match="symbolic link"):
        config.write(link)

    assert link.is_symlink()
    assert target.read_text() == "untouched"


def test_write_keeps_bench_config_private(tmp_path: Path) -> None:
    BenchConfig.write_flat(tmp_path, "private-bench", {"admin_password": "secret"})
    toml_path = BenchConfig.toml_path(tmp_path)
    assert stat.S_IMODE(toml_path.stat().st_mode) == 0o600
    original_owner = (toml_path.stat().st_uid, toml_path.stat().st_gid)

    toml_path.chmod(0o644)
    BenchConfig.write_raw(tmp_path, BenchConfig.read_raw(tmp_path))
    assert stat.S_IMODE(toml_path.stat().st_mode) == 0o600
    assert (toml_path.stat().st_uid, toml_path.stat().st_gid) == original_owner


def test_write_flat_serialises_settings(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    BenchConfig.write_flat(tmp_path, "flatwrite", {"python": "3.13"})
    config = BenchConfig.read(tmp_path)
    assert config.name == "flatwrite"
    assert config.python_version == "3.13"


def test_write_flat_applies_port_offset(tmp_path: Path) -> None:
    BenchConfig.write_flat(tmp_path, "b", {"python": "3.12"}, port_offset=5)
    settings = BenchConfig.read_flat(tmp_path)
    assert settings["python"] == "3.12"
    config = BenchConfig.read(tmp_path)
    assert config.http_port == BenchConfig.default_ports()["http_port"] + 5


def test_write_flat_preserves_production_enabled(tmp_path: Path) -> None:
    """write_flat preserves production.enabled on production benches."""
    BenchConfig.write_flat(
        tmp_path, "prod-bench", {"production_process_manager": "systemd", "admin_domain": "admin.example.com"}
    )
    raw = BenchConfig.read_raw(tmp_path)
    raw["production"]["enabled"] = True
    BenchConfig.write_raw(tmp_path, raw)

    BenchConfig.write_flat(
        tmp_path,
        "prod-bench",
        {
            "production_process_manager": "systemd",
            "admin_domain": "admin.example.com",
            "admin_password": "secret",
        },
    )

    config = BenchConfig.read(tmp_path)
    assert config.production.enabled is True
    assert config.production.process_manager == "systemd"
    assert config.admin.password == "secret"


def test_write_flat_preserves_user_defined_keys(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench.toml"
    BenchConfig.write_raw(
        bench_root,
        {
            "bench": {"name": "custom", "python": "3.14", "custom_flag": True},
            "mariadb": {"root_password": "secret"},
            "custom": {"endpoint": "https://custom.example.com"},
        },
    )

    BenchConfig.write_flat(bench_root, "custom", {"python": "3.13"})

    raw = BenchConfig.read_raw(bench_root)
    assert raw["bench"]["python"] == "3.13"
    assert raw["bench"]["custom_flag"] is True
    assert raw["custom"] == {"endpoint": "https://custom.example.com"}


def test_write_flat_preserves_unknown_array_fields(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench.toml"
    BenchConfig.write_raw(
        bench_root,
        {
            "bench": {"name": "custom", "python": "3.14"},
            "apps": [
                {
                    "name": "frappe",
                    "repo": "https://github.com/frappe/frappe",
                    "branch": "develop",
                    "custom_source": "mirror",
                }
            ],
            "mariadb": {"root_password": "secret"},
        },
    )

    BenchConfig.write_flat(bench_root, "custom", {"app_branch": "version-16"})

    app = BenchConfig.read_raw(bench_root)["apps"][0]
    assert app["branch"] == "version-16"
    assert app["custom_source"] == "mirror"


def test_write_flat_preserves_known_fields_outside_the_flat_update(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench.toml"
    BenchConfig.write_flat(bench_root, "custom", {"admin_password": "secret"})
    raw = BenchConfig.read_raw(bench_root)
    raw["gunicorn"]["workers"] = 1
    BenchConfig.write_raw(bench_root, raw)

    BenchConfig.write_flat(bench_root, "custom", {"python": "3.13"})

    assert BenchConfig.read(bench_root).gunicorn.workers == 1


def test_write_flat_can_clear_a_managed_optional_key(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench.toml"
    BenchConfig.write_flat(
        bench_root,
        "custom",
        {
            "admin_jwks_url": "https://auth.example.com/.well-known/jwks.json",
            "admin_password": "secret",
        },
    )

    BenchConfig.write_flat(bench_root, "custom", {"admin_jwks_url": ""})

    assert "jwks_url" not in BenchConfig.read_raw(bench_root)["admin"]
