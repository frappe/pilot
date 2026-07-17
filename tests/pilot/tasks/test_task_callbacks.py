from __future__ import annotations

import json
from pathlib import Path

import pytest

from pilot.managers import platform
from pilot.tasks import callbacks


def test_remove_failed_site_operation_uses_json_args(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site_path = tmp_path / "sites" / "new.localhost"
    site_path.mkdir(parents=True)
    monkeypatch.setattr(callbacks, "_remove_from_hosts", lambda site: None)

    callbacks.run_callback(
        {"operation": "remove-failed-site", "args": {"site": "new.localhost"}},
        {"bench_root": str(tmp_path)},
    )

    assert not site_path.exists()


def test_remove_failed_site_attempts_database_drop_before_file_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site_path = tmp_path / "sites" / "new.localhost"
    site_path.mkdir(parents=True)
    (site_path / "site_config.json").write_text("{}")
    attempted_while_present = []

    def drop_failed_site(bench_root: Path, site_name: str, path: Path) -> bool:
        attempted_while_present.append(
            (bench_root, site_name, path, path.exists())
        )
        return True

    monkeypatch.setattr(callbacks, "_drop_failed_site", drop_failed_site)
    monkeypatch.setattr(callbacks, "_remove_from_hosts", lambda site: None)

    callbacks.run_callback(
        {"operation": "remove-failed-site", "args": {"site": "new.localhost"}},
        {"bench_root": str(tmp_path)},
    )

    assert attempted_while_present == [
        (tmp_path, "new.localhost", site_path.resolve(), True)
    ]
    assert not site_path.exists()


def test_remove_failed_site_preserves_files_when_database_drop_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site_path = tmp_path / "sites" / "new.localhost"
    site_path.mkdir(parents=True)
    (site_path / "site_config.json").write_text('{"db_name":"recoverable"}')
    monkeypatch.setattr(callbacks, "_drop_failed_site", lambda *args: False)
    monkeypatch.setattr(callbacks, "_remove_from_hosts", lambda site: None)

    with pytest.raises(RuntimeError, match="preserved for recovery"):
        callbacks.run_callback(
            {
                "operation": "remove-failed-site",
                "args": {"site": "new.localhost"},
            },
            {"bench_root": str(tmp_path)},
        )

    assert (site_path / "site_config.json").is_file()


def test_remove_from_hosts_matches_address_and_hostname_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text(
        "127.0.0.1 keep.localhost\n"
        "127.0.0.1 failed.localhost\n"
        "127.0.0.1 failed.localhost.example\n"
    )
    captured = {}
    monkeypatch.setattr(callbacks, "_HOSTS_PATH", hosts_path)
    monkeypatch.setattr(
        platform.subprocess,
        "run",
        lambda argv, **kwargs: captured.update(argv=argv, **kwargs),
    )

    callbacks._remove_from_hosts("failed.localhost")

    assert captured["argv"] == ["sudo", "-n", "tee", str(hosts_path)]
    assert captured["input"].decode() == (
        "127.0.0.1 keep.localhost\n"
        "127.0.0.1 failed.localhost.example\n"
    )


def test_disable_site_ssl_operation_uses_json_args(tmp_path: Path) -> None:
    config_path = tmp_path / "sites" / "secure.localhost" / "site_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"ssl": True, "db_name": "site"}))

    callbacks.run_callback(
        {"operation": "disable-site-ssl", "args": {"site": "secure.localhost"}},
        {"bench_root": str(tmp_path)},
    )

    assert json.loads(config_path.read_text()) == {"ssl": False, "db_name": "site"}


def test_callback_args_must_be_json_serializable(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="JSON serializable"):
        callbacks.validate_callback(
            {"operation": "remove-failed-site", "args": {"path": tmp_path}}
        )


@pytest.mark.parametrize("remove_site", [False, True])
def test_cleanup_site_restore_removes_contained_uploads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remove_site: bool,
) -> None:
    upload_dir = tmp_path / "tmp" / "uploads" / "request-1"
    upload_dir.mkdir(parents=True)
    (upload_dir / "backup.sql").write_text("backup")
    site_path = tmp_path / "sites" / "new.localhost"
    site_path.mkdir(parents=True)
    monkeypatch.setattr(callbacks, "_remove_from_hosts", lambda site: None)

    callbacks.run_callback(
        {
            "operation": "cleanup-site-restore",
            "args": {
                "upload_dir": str(upload_dir),
                "site": "new.localhost",
                "remove_site": remove_site,
            },
        },
        {"bench_root": str(tmp_path)},
    )

    assert not upload_dir.exists()
    assert site_path.exists() is not remove_site


def test_cleanup_site_restore_refuses_outside_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError, match="within the bench"):
        callbacks.run_callback(
            {
                "operation": "cleanup-site-restore",
                "args": {
                    "upload_dir": str(outside),
                    "site": "new.localhost",
                    "remove_site": False,
                },
            },
            {"bench_root": str(tmp_path)},
        )

    assert outside.exists()


def test_cleanup_site_restore_refuses_symlinked_upload_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "uploads").symlink_to(
        outside,
        target_is_directory=True,
    )
    upload_dir = outside / "request-1"
    upload_dir.mkdir()

    with pytest.raises(ValueError, match="root must stay within the bench"):
        callbacks.run_callback(
            {
                "operation": "cleanup-site-restore",
                "args": {
                    "upload_dir": str(upload_dir),
                    "site": "new.localhost",
                    "remove_site": False,
                },
            },
            {"bench_root": str(tmp_path)},
        )

    assert upload_dir.exists()
