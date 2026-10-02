import json

import pytest

from admin.backend.app import create_app
from admin.backend.internal.session import Session
from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.exceptions import BenchError


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
    assert payload["args"]["branch"] == "default"
    assert not (bench.path.parent / "uat").exists()


def test_clone_branch_options_read_the_selected_source_bench(cloning_client, monkeypatch):
    client, bench = cloning_client
    other = bench.path.parent / "other"
    other.mkdir()
    BenchConfig.default("other", benches_root=other.parent).write(other)
    observed = []

    def options(self):
        observed.append(self.path)
        return [{"name": "frappe", "default_branch": "develop", "branches": ["develop", "feature/test"]}]

    monkeypatch.setattr(Bench, "get_clone_branch_options", options)
    response = client.get("/api/v1/benches/other/clone-branch-options")
    assert response.status_code == 200
    assert response.get_json()["apps"][0]["default_branch"] == "develop"
    assert observed == [other]


def test_clone_branch_options_obey_management_guard_and_bench_scope(cloning_client):
    client, bench = cloning_client
    client.set_cookie("sid", Session(bench).issue_site_token("source.localhost"))
    assert client.get("/api/v1/benches/dev/clone-branch-options").status_code == 403
    client.set_cookie("sid", Session(bench).issue_session_token()[0])
    bench.config.admin.allow_bench_management = False
    bench.config.write(bench.path)
    assert client.get("/api/v1/benches/dev/clone-branch-options").status_code == 403


def test_clone_branch_options_report_missing_source_and_origin_errors(cloning_client, monkeypatch):
    client, _ = cloning_client
    assert client.get("/api/v1/benches/missing/clone-branch-options").status_code == 404

    def options(self):
        raise BenchError("Could not read branches for frappe. Check access to origin.")

    monkeypatch.setattr(Bench, "get_clone_branch_options", options)
    response = client.get("/api/v1/benches/dev/clone-branch-options")
    assert response.status_code == 422
    assert "frappe" in response.get_json()["error"]["message"]


def test_clone_branch_overrides_reach_task(cloning_client):
    client, _ = cloning_client
    response = client.post(
        "/api/v1/benches/dev/actions/clone",
        json={"name": "uat", "branch": "current", "app_branches": {"frappe": "develop"}},
    )
    assert response.status_code == 202
    assert response.get_json()["args"]["branch"] == "current"
    assert response.get_json()["args"]["app_branches"] == {"frappe": "develop"}


@pytest.mark.parametrize(
    "selection",
    [{"branch": None}, {"branch": "--bad"}, {"app_branches": []}, {"app_branches": {"frappe": 123}}],
)
def test_invalid_branch_selections_cannot_queue(cloning_client, selection):
    client, _ = cloning_client
    response = client.post("/api/v1/benches/dev/actions/clone", json={"name": "uat", **selection})
    assert response.status_code == 422


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
