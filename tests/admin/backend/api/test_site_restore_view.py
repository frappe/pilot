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


def test_connecting_to_frappe_cloud_shows_the_pass_code_but_not_the_token(tmp_path: Path) -> None:
    response = {
        "token": "fc-secret-token",
        "site": "old.frappe.cloud",
        "approval_url": "https://cloud.example.com/x",
    }
    with patch("pilot.integrations.frappe_cloud.FrappeCloud.request_access", return_value=response):
        reply = _client(tmp_path / "benches" / "current").post(
            "/api/v1/sites/a.localhost/integrations/frappe-cloud", json={"remote_site": "erp.example.com"}
        )

    assert reply.status_code == 200
    body = reply.get_json()
    assert body["status"] == "Pending" and body["remote_site"] == "old.frappe.cloud" and len(body["code"]) == 8
    assert "fc-secret-token" not in reply.get_data(as_text=True)


def test_a_site_token_cannot_connect_to_frappe_cloud(tmp_path: Path) -> None:
    reply = _client(tmp_path / "benches" / "current", site_token="a.localhost").post(
        "/api/v1/sites/a.localhost/integrations/frappe-cloud", json={"remote_site": "erp.example.com"}
    )

    assert reply.status_code == 403


def test_frappe_cloud_refusals_reach_the_user_unchanged(tmp_path: Path) -> None:
    from pilot.exceptions import FrappeCloudError

    with patch(
        "pilot.integrations.frappe_cloud.FrappeCloud.request_access",
        side_effect=FrappeCloudError("No site on Frappe Cloud has the domain erp.example.com."),
    ):
        reply = _client(tmp_path / "benches" / "current").post(
            "/api/v1/sites/a.localhost/integrations/frappe-cloud", json={"remote_site": "erp.example.com"}
        )

    assert reply.status_code == 422
    assert reply.get_json()["error"]["message"] == "No site on Frappe Cloud has the domain erp.example.com."


def test_a_frappe_cloud_restore_keeps_the_download_links_out_of_the_task_record(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    connection = bench_root / "config" / "frappe_cloud" / "a.localhost.json"
    connection.parent.mkdir(parents=True)
    connection.write_text(json.dumps({"url": "https://cloud.example.com", "token": "fc-token-secret"}))
    links = {"database": "https://s3.example.com/db.sql.gz?X-Amz-Signature=signed-secret"}

    with patch("pilot.integrations.frappe_cloud.FrappeCloud.get_download_links", return_value=links) as get_links:
        response = client.post(
            "/api/v1/sites/a.localhost/actions/restore", json={"parts": ["database"], "frappe_cloud_backup": "backup-1"}
        )

    assert response.status_code == 202
    get_links.assert_called_once_with("backup-1")
    meta = (bench_root / "tasks" / response.get_json()["task_id"] / "meta.json").read_text()
    assert json.loads(meta)["args"]["frappe_cloud_backup"] == "backup-1"
    assert "signed-secret" not in meta and "fc-token-secret" not in meta


def test_frappe_cloud_backups_include_the_running_backup_so_a_reloaded_page_can_follow_it(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    connection = bench_root / "config" / "frappe_cloud" / "a.localhost.json"
    connection.parent.mkdir(parents=True)
    connection.write_text(json.dumps({"url": "https://cloud.example.com", "token": "t"}))
    backups = [{"name": "backup-1", "created_at": "2026-10-04T12:00:00+05:30", "size": 2048}]

    with (
        patch("pilot.integrations.frappe_cloud.FrappeCloud.get_backups", return_value=backups),
        patch("pilot.integrations.frappe_cloud.FrappeCloud.get_running_backup", return_value="backup-2"),
    ):
        reply = client.get("/api/v1/sites/a.localhost/integrations/frappe-cloud/backups")

    assert reply.get_json() == {
        "backups": [{"name": "backup-1", "created_at": "2026-10-04T12:00:00+05:30", "size_bytes": 2048}],
        "running_backup": "backup-2",
    }


def test_cancelling_frappe_cloud_access_revokes_the_request_and_forgets_the_token(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    connection = bench_root / "config" / "frappe_cloud" / "a.localhost.json"
    connection.parent.mkdir(parents=True)
    connection.write_text(json.dumps({"url": "https://cloud.example.com", "token": "t"}))

    with patch("pilot.integrations.frappe_cloud.FrappeCloud.revoke") as revoke:
        reply = client.delete("/api/v1/sites/a.localhost/integrations/frappe-cloud")

    assert reply.status_code == 200
    revoke.assert_called_once()
    assert not connection.exists()


def test_a_frappe_cloud_backup_status_carries_the_link_to_its_job(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    connection = bench_root / "config" / "frappe_cloud" / "a.localhost.json"
    connection.parent.mkdir(parents=True)
    connection.write_text(json.dumps({"url": "https://cloud.example.com", "token": "t"}))
    status = {"status": "Running", "job_url": "https://cloud.example.com/dashboard/sites/s/insights/jobs/j"}

    with patch("pilot.integrations.frappe_cloud.FrappeCloud.get_backup_status", return_value=status):
        reply = client.get("/api/v1/sites/a.localhost/integrations/frappe-cloud/backups/backup-2")

    assert reply.get_json() == {"name": "backup-2", **status}


def test_frappe_cloud_backups_load_the_page_that_starts_at_the_given_offset(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    connection = bench_root / "config" / "frappe_cloud" / "a.localhost.json"
    connection.parent.mkdir(parents=True)
    connection.write_text(json.dumps({"url": "https://cloud.example.com", "token": "t"}))

    with (
        patch("pilot.integrations.frappe_cloud.FrappeCloud.get_backups", return_value=[]) as get_backups,
        patch("pilot.integrations.frappe_cloud.FrappeCloud.get_running_backup", return_value=None),
    ):
        client.get("/api/v1/sites/a.localhost/integrations/frappe-cloud/backups?start=5")

    get_backups.assert_called_once_with(5)


def test_a_restore_can_choose_the_site_config_and_skip_failing_patches(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    response = _client(bench_root).post(
        "/api/v1/sites/a.localhost/actions/restore",
        json={"parts": ["config", "database"], "source_site": "b.localhost", "skip_failing_patches": True},
    )

    assert response.status_code == 202
    args = _meta(bench_root, response)["args"]
    assert args["parts"] == ["database", "config"] and args["skip_failing_patches"] is True


def test_an_upload_restore_reads_skip_failing_patches_from_the_form(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    response = _client(bench_root).post(
        "/api/v1/sites/a.localhost/actions/restore-upload",
        data={"parts": ["config"], "skip_failing_patches": "true", "config": (io.BytesIO(b"{}"), "x-site_config_backup.json")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    assert _meta(bench_root, response)["args"]["skip_failing_patches"] is True


def test_a_chunked_upload_restores_once_every_file_has_arrived(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    started = client.post(
        "/api/v1/sites/a.localhost/uploads", json={"files": {"config": {"filename": "x-site_config_backup.json", "size": 2}}}
    ).get_json()
    url = f"/api/v1/sites/a.localhost/uploads/{started['upload_id']}"

    early = client.post("/api/v1/sites/a.localhost/actions/restore", json={"parts": ["config"], "upload_id": started["upload_id"]})
    sent = client.put(f"{url}/files/config?offset=0", data=b"{}", content_type="application/octet-stream")
    response = client.post("/api/v1/sites/a.localhost/actions/restore", json={"parts": ["config"], "upload_id": started["upload_id"]})

    assert early.status_code == 422
    assert sent.get_json() == {"received": 2}
    assert response.status_code == 202
    assert _meta(bench_root, response)["args"]["upload_dir"].endswith(started["upload_id"])


def test_a_chunk_past_the_received_bytes_returns_a_conflict(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    upload_id = client.post(
        "/api/v1/sites/a.localhost/uploads", json={"files": {"database": {"filename": "x.sql.gz", "size": 10}}}
    ).get_json()["upload_id"]

    reply = client.put(f"/api/v1/sites/a.localhost/uploads/{upload_id}/files/database?offset=4", data=b"ab")

    assert reply.status_code == 409


def test_a_site_token_cannot_use_another_sites_upload(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    upload_id = _client(bench_root).post(
        "/api/v1/sites/b.localhost/uploads", json={"files": {"database": {"filename": "x.sql.gz", "size": 10}}}
    ).get_json()["upload_id"]

    reply = _client(bench_root, site_token="a.localhost").get(f"/api/v1/sites/a.localhost/uploads/{upload_id}")

    assert reply.status_code == 422
    assert reply.get_json()["error"]["message"] == "The upload does not exist."
