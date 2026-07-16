"""Tests for /api/v1/sites/<name>/backups and /backup-schedule routes."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

from pilot.config.bench_toml_builder import BenchTomlBuilder


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from pilot.commands.generate_session import ensure_jwt_secret, issue_token

    bench_root.mkdir(parents=True, exist_ok=True)
    (bench_root / "bench.toml").write_text(
        BenchTomlBuilder(bench_root.name, {"admin_enabled": True, "admin_password": password}).render()
    )
    secret = ensure_jwt_secret(bench_root / "bench.toml")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))
    return client


def _make_site(bench_root: Path, name: str, **config) -> None:
    site_dir = bench_root / "sites" / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps(config))


def _make_backup_file(bench_root: Path, site: str, timestamp: str, suffix: str) -> Path:
    backups_dir = bench_root / "sites" / site / "private" / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    path = backups_dir / f"{timestamp}-{site}-{suffix}"
    path.write_text("data")
    return path


def _request(client, method, path, **kwargs):
    with patch(
        "pilot.tasks.manager.task_runner.task_workers.wake",
        return_value=False,
    ):
        return getattr(client, method)(path, **kwargs)


def test_backup_site_queues_task(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)

    response = _request(client, "post", "/api/v1/sites/site.localhost/backups")

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "backup-site"
    assert body["args"] == {"site": "site.localhost", "with_files": True}


def test_backup_site_rejects_missing_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = _request(client, "post", "/api/v1/sites/missing.localhost/backups")

    assert response.status_code == 404


def test_list_backups_includes_local_files(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    _make_backup_file(bench_root, "site.localhost", "20240101_000000", "database.sql.gz")
    client = _client(bench_root)

    response = client.get("/api/v1/sites/site.localhost/backups")

    body = response.get_json()
    assert response.status_code == 200
    assert body[0]["timestamp"] == "20240101_000000"
    assert body[0]["files"][0]["kind"] == "database"


def test_get_backup_returns_the_matching_set(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    _make_backup_file(bench_root, "site.localhost", "20240101_000000", "database.sql.gz")
    client = _client(bench_root)

    response = client.get("/api/v1/sites/site.localhost/backups/20240101_000000")

    assert response.status_code == 200
    assert response.get_json()["timestamp"] == "20240101_000000"


def test_get_backup_404s_for_an_unknown_timestamp(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)

    response = client.get("/api/v1/sites/site.localhost/backups/20240101_000000")

    assert response.status_code == 404


def test_download_backup_file_serves_the_file(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    _make_backup_file(bench_root, "site.localhost", "20240101_000000", "database.sql.gz")
    client = _client(bench_root)

    response = client.get(
        "/api/v1/sites/site.localhost/backups/20240101_000000/files/"
        "20240101_000000-site.localhost-database.sql.gz/content"
    )

    assert response.status_code == 200
    assert response.data == b"data"


def test_download_backup_file_rejects_a_timestamp_mismatch(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    _make_backup_file(bench_root, "site.localhost", "20240101_000000", "database.sql.gz")
    client = _client(bench_root)

    response = client.get(
        "/api/v1/sites/site.localhost/backups/20240102_000000/files/"
        "20240101_000000-site.localhost-database.sql.gz/content"
    )

    assert response.status_code == 422


def test_download_backup_file_rejects_a_dotfile(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)

    response = client.get(
        "/api/v1/sites/site.localhost/backups/20240101_000000/files/.hidden/content"
    )

    assert response.status_code == 422


def test_backup_download_links_returns_urls_directly(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)
    offsite = Mock()
    offsite.get_backup.return_value = {"database": "20240101_000000-database.sql.gz"}
    offsite.presigned_url.return_value = "https://bucket.example/signed"

    with patch(
        "pilot.integrations.s3.backups.OffsiteBackup.from_config",
        return_value=offsite,
    ):
        response = client.get(
            "/api/v1/sites/site.localhost/backups/20240101_000000/download-links"
        )

    assert response.status_code == 200
    assert response.get_json() == {"database": "https://bucket.example/signed"}


def test_backup_schedule_put_returns_the_saved_resource(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)

    with patch("admin.backend.cron_manager.CronManager.get_schedule", return_value="0 2 * * *"), \
         patch("admin.backend.cron_manager.CronManager.set_schedule") as set_schedule:
        response = client.put(
            "/api/v1/sites/site.localhost/backup-schedule",
            json={"schedule": "0 2 * * *", "retention": {"scheme": "fifo", "keep_last": 5}},
        )

    assert response.status_code == 200
    body = response.get_json()
    assert body["schedule"] == "0 2 * * *"
    assert body["retention"]["keep_last"] == 5
    set_schedule.assert_called_once()


def test_backup_schedule_put_rejects_invalid_cron(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)

    response = client.put(
        "/api/v1/sites/site.localhost/backup-schedule",
        json={"schedule": "not-a-cron"},
    )

    assert response.status_code == 422


def test_backup_schedule_delete_returns_no_content(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)

    with patch("admin.backend.cron_manager.CronManager.remove_schedule") as remove_schedule:
        response = client.delete("/api/v1/sites/site.localhost/backup-schedule")

    assert response.status_code == 204
    assert response.data == b""
    remove_schedule.assert_called_once_with("site.localhost")
