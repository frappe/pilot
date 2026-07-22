from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tests.admin.backend.test_admin_app import _client


def _write_site(bench_root: Path, name: str) -> None:
    site_path = bench_root / "sites" / name
    site_path.mkdir(parents=True)
    (site_path / "site_config.json").write_text(json.dumps({"installed_apps": []}))


def test_delete_site_returns_accepted_task_resource(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    _write_site(bench_root, "s.localhost")

    with patch(
        "pilot.internal.tasks.runner.task_workers.wake",
        return_value=False,
    ):
        response = client.delete("/api/v1/sites/s.localhost")

    body = response.get_json()
    assert response.status_code == 202
    assert response.headers["Location"] == f"/api/v1/tasks/{body['task_id']}"
    assert body["command"] == "drop-site"
    assert body["args"] == {"site": "s.localhost"}


def test_delete_site_returns_not_found_without_starting_task(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    with patch("admin.backend.api.v1.sites.core.DropSiteTask.queue") as queue:
        response = client.delete("/api/v1/sites/missing.localhost")

    assert response.status_code == 404
    queue.assert_not_called()


def test_delete_site_rejects_symlink_without_starting_task(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    outside = tmp_path / "outside"
    _write_site(outside, "linked.localhost")
    sites = bench_root / "sites"
    sites.mkdir()
    (sites / "linked.localhost").symlink_to(outside / "sites" / "linked.localhost", target_is_directory=True)

    with patch("admin.backend.api.v1.sites.core.DropSiteTask.queue") as queue:
        response = client.delete("/api/v1/sites/linked.localhost")

    assert response.status_code == 404
    queue.assert_not_called()


def test_same_site_mutations_cannot_queue_together(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    with (
        patch("admin.backend.api.v1.sites.core.new_site_name_error", return_value=None),
        patch(
            "pilot.internal.tasks.runner.task_workers.wake",
            return_value=False,
        ),
    ):
        first = client.post("/api/v1/sites", json={"name": "s.localhost"})
        conflict = client.post("/api/v1/sites", json={"name": "s.localhost"})

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.get_json()["error"]["code"] == "task_conflict"


def test_invalid_idempotency_key_is_a_validation_error(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    with patch("admin.backend.api.v1.sites.core.new_site_name_error", return_value=None):
        response = client.post(
            "/api/v1/sites",
            json={"name": "s.localhost"},
            headers={"Idempotency-Key": "x" * 256},
        )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_task"


def test_site_creation_rejects_symlinked_sites_root(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    outside = tmp_path / "outside-sites"
    outside.mkdir()
    (bench_root / "sites").symlink_to(outside, target_is_directory=True)

    with patch("admin.backend.api.v1.sites.core.NewSiteTask.queue") as queue:
        create = client.post(
            "/api/v1/sites",
            json={"name": "s.localhost"},
        )

    assert create.status_code == 422
    queue.assert_not_called()
