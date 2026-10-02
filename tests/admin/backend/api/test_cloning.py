import json

import pytest

from admin.backend.app import create_app
from admin.backend.internal.session import Session
from pilot.config import BenchConfig
from pilot.core.bench import Bench


@pytest.fixture
def cloning_client(tmp_path, monkeypatch):
    root = tmp_path / "benches/dev"
    root.mkdir(parents=True)
    config = BenchConfig.default("dev", benches_root=root.parent)
    config.admin.allow_bench_management = True
    config.admin.enabled = True
    config.write(root)
    bench = Bench(root)
    bench.create_directories()
    site = bench.site("source.localhost")
    site.path.mkdir()
    (site.path / "site_config.json").write_text(json.dumps({"db_name": "source_db"}))
    monkeypatch.setattr("pilot.internal.tasks.worker.task_workers.wake", lambda *args: None)
    monkeypatch.setattr("pilot.core.site.clone.query_installed_apps_via_db", lambda *args: [])
    app = create_app(root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", Session(bench).issue_session_token()[0])
    return client, bench


def test_clone_bench_queues_a_real_task(cloning_client):
    client, bench = cloning_client
    response = client.post("/api/v1/benches/dev/actions/clone", json={"name": "uat"})
    assert response.status_code == 202
    payload = response.get_json()
    assert payload["command"] == "clone-bench"
    assert payload["args"]["source_bench"] == "dev"
    assert payload["args"]["name"] == "uat"
    assert not (bench.path.parent / "uat").exists()


def test_conflicting_clone_returns_conflict(cloning_client):
    client, _ = cloning_client
    data = {"name": "uat"}
    assert client.post("/api/v1/benches/dev/actions/clone", json=data).status_code == 202
    assert client.post("/api/v1/benches/dev/actions/clone", json=data).status_code == 409


def test_clone_site_queues_private_password_and_is_idempotent(cloning_client):
    client, _ = cloning_client
    data = {"name": "uat.localhost", "target_bench": "dev"}
    first = client.post(
        "/api/v1/sites/source.localhost/actions/clone", json=data, headers={"Idempotency-Key": "clone-one"}
    )
    second = client.post(
        "/api/v1/sites/source.localhost/actions/clone", json=data, headers={"Idempotency-Key": "clone-one"}
    )
    assert first.status_code == second.status_code == 202
    assert first.get_json()["task_id"] == second.get_json()["task_id"]
    assert first.get_json()["command"] == "clone-site"
    assert first.get_json()["args"]["admin_password"] == "[redacted]"


@pytest.mark.parametrize("target", ["../outside", "pilot", "123", ""])
def test_invalid_bench_targets_cannot_queue(cloning_client, target):
    client, _ = cloning_client
    assert client.post("/api/v1/benches/dev/actions/clone", json={"name": target}).status_code == 422


def test_cloning_requires_bench_scope(cloning_client):
    client, bench = cloning_client
    client.set_cookie("sid", Session(bench).issue_site_token("source.localhost"))
    assert (
        client.post(
            "/api/v1/sites/source.localhost/actions/clone",
            json={"name": "uat.localhost", "target_bench": "dev"},
        ).status_code
        == 403
    )
    assert client.post("/api/v1/benches/dev/actions/clone", json={"name": "uat"}).status_code == 403


def test_cross_bench_cloning_obeys_management_guard(cloning_client):
    client, bench = cloning_client
    bench.config.admin.allow_bench_management = False
    bench.config.write(bench.path)
    assert (
        client.post(
            "/api/v1/sites/source.localhost/actions/clone",
            json={"name": "uat.localhost", "target_bench": "other"},
        ).status_code
        == 403
    )
    assert client.post("/api/v1/benches/dev/actions/clone", json={"name": "uat"}).status_code == 403
    assert (
        client.post(
            "/api/v1/sites/source.localhost/actions/clone",
            json={"name": "uat.localhost", "target_bench": "dev"},
        ).status_code
        == 202
    )
