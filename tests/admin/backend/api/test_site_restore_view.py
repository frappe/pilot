from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import BenchConfig


def _client(bench_root: Path, site_token: str = ""):
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    bench_root.mkdir(parents=True, exist_ok=True)
    (bench_root / "bench.toml").write_text(
        BenchConfig.from_flat(bench_root.name, {"admin_enabled": True, "admin_password": "secret"}).dumps()
    )
    for site in ("a.localhost", "b.localhost"):
        (bench_root / "sites" / site).mkdir(parents=True, exist_ok=True)
        (bench_root / "sites" / site / "site_config.json").write_text("{}")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    session = Session(Bench(bench_root))
    client.set_cookie("sid", session.issue_site_token(site_token) if site_token else session.issue_session_token()[0])
    return client


@pytest.fixture(autouse=True)
def _no_worker():
    with patch("pilot.internal.tasks.runner.task_workers.wake", return_value=False):
        yield


def _meta(bench_root: Path, response) -> dict:
    return json.loads((bench_root / "tasks" / response.get_json()["task_id"] / "meta.json").read_text())


def test_restore_from_another_site_locks_both_sites(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    response = _client(bench_root).post(
        "/api/v1/sites/a.localhost/actions/restore",
        json={"parts": ["private", "database"], "source_site": "b.localhost", "backup_timestamp": "20261004_010000"},
    )

    assert response.status_code == 202
    meta = _meta(bench_root, response)
    assert meta["args"]["parts"] == ["database", "private"]
    assert set(meta["resource_keys"]) == {"site:a.localhost", "site:b.localhost"}


def test_a_site_token_cannot_read_another_sites_backup(tmp_path: Path) -> None:
    response = _client(tmp_path / "benches" / "current", site_token="a.localhost").post(
        "/api/v1/sites/a.localhost/actions/restore", json={"parts": ["database"], "source_site": "b.localhost"}
    )

    assert response.status_code == 403


@pytest.mark.parametrize("parts", [[], ["database", "logs"], "database"])
def test_unknown_parts_are_rejected(tmp_path: Path, parts) -> None:
    response = _client(tmp_path / "benches" / "current").post(
        "/api/v1/sites/a.localhost/actions/restore", json={"parts": parts}
    )

    assert response.status_code == 422


def test_remote_credentials_are_checked_before_queueing(tmp_path: Path) -> None:
    from pilot.exceptions import RemoteSiteError

    with patch(
        "pilot.integrations.frappe_site.RemoteFrappeSite.login",
        side_effect=RemoteSiteError("The administrator password is invalid."),
    ):
        response = _client(tmp_path / "benches" / "current").post(
            "/api/v1/sites/a.localhost/actions/restore",
            json={"parts": ["database"], "remote_site": "old.example.com", "password": "nope", "backup_timestamp": "20261004_020000"},
        )

    assert response.status_code == 422
    assert response.get_json()["error"]["message"] == "The administrator password is invalid."


def test_the_remote_password_stays_out_of_the_task_record(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    with patch("pilot.integrations.frappe_site.RemoteFrappeSite.login"):
        response = _client(bench_root).post(
            "/api/v1/sites/a.localhost/actions/restore",
            json={"parts": ["database"], "remote_site": "old.example.com", "password": "hunter2-secret", "backup_timestamp": "20261004_020000"},
        )

    assert response.status_code == 202
    assert "hunter2-secret" not in (bench_root / "tasks" / response.get_json()["task_id"] / "meta.json").read_text()


def test_uploads_are_saved_under_names_that_tell_the_parts_apart(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    response = _client(bench_root).post(
        "/api/v1/sites/a.localhost/actions/restore-upload",
        data={
            "parts": ["database", "public"],
            "database": (io.BytesIO(b"sql"), "my backup.sql.gz"),
            "public": (io.BytesIO(b"tar"), "files.tar"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    upload_dir = Path(_meta(bench_root, response)["args"]["upload_dir"])
    assert sorted(path.name for path in upload_dir.iterdir()) == ["upload-database.sql.gz", "upload-files.tar"]


def test_an_upload_missing_a_chosen_part_is_rejected(tmp_path: Path) -> None:
    response = _client(tmp_path / "benches" / "current").post(
        "/api/v1/sites/a.localhost/actions/restore-upload",
        data={"parts": ["database"]},
        content_type="multipart/form-data",
    )

    assert response.status_code == 422


def test_a_site_token_cannot_point_the_admin_at_another_host(tmp_path: Path) -> None:
    response = _client(tmp_path / "benches" / "current", site_token="a.localhost").post(
        "/api/v1/sites/a.localhost/actions/restore",
        json={"parts": ["database"], "remote_site": "http://127.0.0.1:6379", "password": "x", "backup_timestamp": "20261004_020000"},
    )

    assert response.status_code == 403


@pytest.mark.parametrize("remote", ["foo bar.com", "https://[::1"])
def test_an_invalid_remote_site_is_a_clear_error(tmp_path: Path, remote: str) -> None:
    response = _client(tmp_path / "benches" / "current").post(
        "/api/v1/sites/a.localhost/actions/restore",
        json={"parts": ["database"], "remote_site": remote, "password": "x", "backup_timestamp": "20261004_020000"},
    )

    assert response.status_code == 422


def test_an_upload_that_cannot_be_queued_is_deleted(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    with patch("pilot.tasks.restore_site.RestoreSiteTask.queue", side_effect=ValueError("busy")):
        response = client.post(
            "/api/v1/sites/a.localhost/actions/restore-upload",
            data={"parts": ["database"], "database": (io.BytesIO(b"sql"), "x.sql.gz")},
            content_type="multipart/form-data",
        )

    assert response.status_code >= 400
    assert list((bench_root / "tmp" / "uploads").iterdir()) == []


def test_remote_backups_list_the_latest_run(tmp_path: Path) -> None:
    files = {"database": "./s/20261004_020000-s-database.sql.gz", "private": "./s/20261004_020000-s-private-files.tar"}
    with (
        patch("pilot.integrations.frappe_site.RemoteFrappeSite.login"),
        patch("pilot.integrations.frappe_site.RemoteFrappeSite.get_latest_run", return_value=("20261004_020000", files)),
    ):
        response = _client(tmp_path / "benches" / "current").post(
            "/api/v1/sites/a.localhost/actions/remote-backups",
            json={"remote_site": "old.example.com", "password": "pw"},
        )

    assert response.status_code == 200
    assert response.get_json() == {
        "backups": [{"timestamp": "20261004_020000", "created_at": "2026-10-04T02:00:00+00:00", "parts": ["database", "private"]}]
    }


def test_a_remote_without_backups_lists_none(tmp_path: Path) -> None:
    with (
        patch("pilot.integrations.frappe_site.RemoteFrappeSite.login"),
        patch("pilot.integrations.frappe_site.RemoteFrappeSite.get_latest_run", return_value=("", {})),
    ):
        response = _client(tmp_path / "benches" / "current").post(
            "/api/v1/sites/a.localhost/actions/remote-backups",
            json={"remote_site": "old.example.com", "password": "pw"},
        )

    assert response.get_json() == {"backups": []}


def test_a_chosen_remote_backup_is_passed_to_the_task(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    with patch("pilot.integrations.frappe_site.RemoteFrappeSite.login"):
        response = _client(bench_root).post(
            "/api/v1/sites/a.localhost/actions/restore",
            json={"parts": ["database"], "remote_site": "old.example.com", "password": "pw", "backup_timestamp": "20261004_020000"},
        )

    assert response.status_code == 202
    assert _meta(bench_root, response)["args"]["backup_timestamp"] == "20261004_020000"


def test_a_remote_restore_needs_a_backup_from_remote_backups(tmp_path: Path) -> None:
    with patch("pilot.integrations.frappe_site.RemoteFrappeSite.login") as login:
        response = _client(tmp_path / "benches" / "current").post(
            "/api/v1/sites/a.localhost/actions/restore",
            json={"parts": ["database"], "remote_site": "old.example.com", "password": "pw"},
        )

    assert response.status_code == 422
    login.assert_not_called()
