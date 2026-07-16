"""Tests for BenchTomlStore, the single read/write entry point for bench.toml."""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

import pytest

from pilot.config.bench_toml_builder import BenchTomlBuilder
from pilot.config.toml_store import BenchTomlStore
from pilot.exceptions import ConfigError
from pilot.internal.atomic_file import exclusive_file_lock


def _write_bench(bench_dir: Path, name: str = "test") -> BenchTomlStore:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(BenchTomlBuilder(name).render())
    return BenchTomlStore.for_bench(bench_dir)


def test_for_bench_resolves_toml_path(tmp_path: Path) -> None:
    store = BenchTomlStore.for_bench(tmp_path)
    assert store.path == tmp_path / "bench.toml"


def test_accepts_directory_or_file(tmp_path: Path) -> None:
    (tmp_path / "bench.toml").write_text(BenchTomlBuilder("x").render())
    assert BenchTomlStore(tmp_path).path == BenchTomlStore(tmp_path / "bench.toml").path


def test_exists_reflects_file(tmp_path: Path) -> None:
    store = BenchTomlStore.for_bench(tmp_path)
    assert not store.exists()
    _write_bench(tmp_path)
    assert store.exists()


def test_read_returns_validated_config(tmp_path: Path) -> None:
    store = _write_bench(tmp_path, "mybench")
    assert store.read().name == "mybench"


def test_read_no_validate_allows_half_configured(tmp_path: Path) -> None:
    (tmp_path / "bench.toml").write_text('[bench]\nname = "half"\n')
    store = BenchTomlStore.for_bench(tmp_path)
    with pytest.raises(ConfigError):
        store.read()
    assert store.read(validate=False).name == "half"


def test_read_raw_preserves_unmodeled_sections(tmp_path: Path) -> None:
    store = _write_bench(tmp_path)
    raw = store.read_raw()
    raw["sites"] = [{"name": "site1"}]
    store.write_raw(raw)
    assert store.read_raw()["sites"] == [{"name": "site1"}]


def test_read_flat_matches_builder(tmp_path: Path) -> None:
    store = _write_bench(tmp_path, "flatbench")
    assert store.read_flat()["bench_name"] == "flatbench"


def test_write_round_trips_config(tmp_path: Path) -> None:
    store = _write_bench(tmp_path, "rt")
    config = store.read()
    config.http_port = 8123
    store.write(config)
    assert store.read().http_port == 8123


def test_write_rejects_invalid_config_without_replacing_file(tmp_path: Path) -> None:
    store = _write_bench(tmp_path, "valid")
    original = store.path.read_bytes()
    config = store.read()
    config.http_port = 0

    with pytest.raises(ConfigError):
        store.write(config)

    assert store.path.read_bytes() == original


def test_write_raw_validates_known_config_without_dropping_custom_keys(tmp_path: Path) -> None:
    store = _write_bench(tmp_path, "valid")
    raw = store.read_raw()
    raw["custom_plugin"] = {"operator_key": "kept"}
    store.write_raw(raw)

    assert store.read_raw()["custom_plugin"] == {"operator_key": "kept"}

    original = store.path.read_bytes()
    raw["bench"]["http_port"] = 0
    with pytest.raises(ConfigError):
        store.write_raw(raw)
    assert store.path.read_bytes() == original


def test_failed_atomic_replace_keeps_existing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _write_bench(tmp_path, "stable")
    original = store.path.read_bytes()
    config = store.read()
    config.http_port = 8123

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr("pilot.internal.atomic_file.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        store.write(config)

    assert store.path.read_bytes() == original
    assert not list(tmp_path.glob(".bench.toml.*.tmp"))


def test_write_fsyncs_file_and_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _write_bench(tmp_path, "durable")
    config = store.read()
    events = []
    real_replace = os.replace

    def replace(source, destination):
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(
        "pilot.internal.atomic_file.os.fsync", lambda descriptor: events.append("fsync")
    )
    monkeypatch.setattr("pilot.internal.atomic_file.os.replace", replace)

    store.write(config)

    assert events == ["fsync", "replace", "fsync"]


def test_concurrent_raw_edits_preserve_both_updates(tmp_path: Path) -> None:
    store = _write_bench(tmp_path, "locked")
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first_edit() -> None:
        with store.edit_raw() as raw:
            raw.setdefault("custom", {})["first"] = True
            first_entered.set()
            assert release_first.wait(timeout=1)

    def second_edit() -> None:
        assert first_entered.wait(timeout=1)
        with store.edit_raw() as raw:
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

    assert store.read_raw()["custom"] == {"first": True, "second": True}
    lock_metadata = (tmp_path / ".bench.toml.lock").stat()
    config_metadata = store.path.stat()
    assert stat.S_IMODE(lock_metadata.st_mode) == 0o600
    assert lock_metadata.st_uid == config_metadata.st_uid


def test_nonblocking_lock_fails_when_another_thread_holds_it(tmp_path: Path) -> None:
    target = tmp_path / "operation"

    with exclusive_file_lock(target):
        with pytest.raises(BlockingIOError):
            with exclusive_file_lock(target, blocking=False):
                pass


def test_unchanged_transaction_does_not_replace_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _write_bench(tmp_path, "unchanged")

    def fail_replace(source, destination):
        raise AssertionError("unchanged transaction replaced the file")

    monkeypatch.setattr("pilot.internal.atomic_file.os.replace", fail_replace)

    with store.edit_raw():
        pass


def test_atomic_write_refuses_symbolic_link_destination(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config = _write_bench(config_dir, "safe").read()
    target = tmp_path / "target.toml"
    target.write_text("untouched")
    link = tmp_path / "bench.toml"
    link.symlink_to(target)

    with pytest.raises(OSError, match="symbolic link"):
        BenchTomlStore(link).write(config)

    assert link.is_symlink()
    assert target.read_text() == "untouched"


def test_write_keeps_bench_config_private(tmp_path: Path) -> None:
    store = BenchTomlStore.for_bench(tmp_path)
    store.write_flat("private-bench", {"admin_password": "secret"})
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    original_owner = (store.path.stat().st_uid, store.path.stat().st_gid)

    store.path.chmod(0o644)
    store.write_raw(store.read_raw())
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert (store.path.stat().st_uid, store.path.stat().st_gid) == original_owner


def test_write_flat_serialises_settings(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = BenchTomlStore.for_bench(tmp_path)
    store.write_flat("flatwrite", {"python": "3.13"})
    config = store.read()
    assert config.name == "flatwrite"
    assert config.python_version == "3.13"


def test_write_flat_matches_builder(tmp_path: Path) -> None:
    store = BenchTomlStore.for_bench(tmp_path)
    store.write_flat("b", {"python": "3.12"}, port_offset=5)
    assert store.read_flat() == BenchTomlBuilder.read_settings(store.path)


def test_write_flat_preserves_production_enabled(tmp_path: Path) -> None:
    """production.enabled has no flat key, so BenchTomlBuilder.build() always
    reconstructs it as the dataclass default (False). A wizard/settings save
    (write_flat) on a bench already brought up to production must not silently
    demote it back to "development" — that's a regression, not a real change."""
    store = BenchTomlStore.for_bench(tmp_path)
    store.write_flat("prod-bench", {"production_process_manager": "systemd", "admin_domain": "admin.example.com"})
    raw = store.read_raw()
    raw["production"]["enabled"] = True
    store.write_raw(raw)

    store.write_flat("prod-bench", {
        "production_process_manager": "systemd",
        "admin_domain": "admin.example.com",
        "admin_password": "secret",
    })

    config = store.read()
    assert config.production.enabled is True
    assert config.production.process_manager == "systemd"
    assert config.admin.password == "secret"


def test_write_flat_preserves_user_defined_keys(tmp_path: Path) -> None:
    store = BenchTomlStore(tmp_path / "bench.toml")
    store.write_raw(
        {
            "bench": {"name": "custom", "python": "3.14", "custom_flag": True},
            "mariadb": {"root_password": "secret"},
            "custom": {"endpoint": "https://custom.example.com"},
        }
    )

    store.write_flat("custom", {"python": "3.13"})

    raw = store.read_raw()
    assert raw["bench"]["python"] == "3.13"
    assert raw["bench"]["custom_flag"] is True
    assert raw["custom"] == {"endpoint": "https://custom.example.com"}


def test_write_flat_preserves_unknown_array_fields(tmp_path: Path) -> None:
    store = BenchTomlStore(tmp_path / "bench.toml")
    store.write_raw(
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
        }
    )

    store.write_flat("custom", {"app_branch": "version-16"})

    app = store.read_raw()["apps"][0]
    assert app["branch"] == "version-16"
    assert app["custom_source"] == "mirror"


def test_write_flat_preserves_known_fields_outside_the_flat_update(tmp_path: Path) -> None:
    store = BenchTomlStore(tmp_path / "bench.toml")
    store.write_flat("custom", {"admin_password": "secret"})
    raw = store.read_raw()
    raw["gunicorn"]["workers"] = 1
    store.write_raw(raw)

    store.write_flat("custom", {"python": "3.13"})

    assert store.read().gunicorn.workers == 1


def test_write_flat_can_clear_a_managed_optional_key(tmp_path: Path) -> None:
    store = BenchTomlStore(tmp_path / "bench.toml")
    store.write_flat(
        "custom",
        {
            "admin_jwks_url": "https://auth.example.com/.well-known/jwks.json",
            "admin_password": "secret",
        },
    )

    store.write_flat("custom", {"admin_jwks_url": ""})

    assert "jwks_url" not in store.read_raw()["admin"]
