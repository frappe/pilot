from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

from admin.backend.app import create_app
from pilot.config import BenchConfig
from pilot.internal.tasks.store import TaskStore
from pilot.managers.task.models import TaskStatus


def setup_client(bench_root: Path):
    app = create_app(bench_root)
    app.config["TESTING"] = True
    return app.test_client()


def save_configuration(client):
    return client.put(
        "/api/v1/setup/configuration",
        json={
            "admin_password": "admin-secret",
            "mariadb_password": "database-secret",
        },
    )


def start_setup(client, key: str = "wizard-setup"):
    with patch("pilot.internal.tasks.runner.task_workers.wake"):
        return client.post(
            "/api/v1/setup/actions/start",
            headers={"Idempotency-Key": key},
        )


def complete_task(bench_root: Path, task_id: str) -> None:
    store = TaskStore(bench_root)
    store.transition(
        task_id,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        {"started_at": "2026-07-15T12:00:01+00:00"},
    )
    store.transition(
        task_id,
        TaskStatus.RUNNING,
        TaskStatus.SUCCESS,
        {
            "finished_at": "2026-07-15T12:00:02+00:00",
            "exit_code": 0,
        },
    )


def fail_task(bench_root: Path, task_id: str) -> None:
    store = TaskStore(bench_root)
    store.transition(task_id, TaskStatus.QUEUED, TaskStatus.RUNNING)
    store.transition(
        task_id,
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
        {"finished_at": "2026-07-15T12:00:02+00:00", "exit_code": 1},
    )


def test_configuration_update_is_sanitized_and_preserves_unknown_keys(
    tmp_path: Path,
) -> None:
    client = setup_client(tmp_path)
    first = save_configuration(client)
    with BenchConfig.open(tmp_path, mode="raw") as config:
        config["plugin"] = {"custom_key": "custom-value"}

    response = client.put(
        "/api/v1/setup/configuration",
        json={"app_branch": "develop"},
    )

    assert first.status_code == 200
    assert response.status_code == 200
    assert response.get_json()["app_branch"] == "develop"
    assert response.get_json()["admin_password_configured"] is True
    assert response.get_json()["mariadb_password_configured"] is True
    assert response.get_json()["postgres_password_configured"] is False
    assert "admin_password" not in response.get_json()
    assert "mariadb_password" not in response.get_json()
    assert BenchConfig.read(tmp_path).admin.password == "admin-secret"
    assert BenchConfig.read(tmp_path).mariadb.root_password == "database-secret"
    assert BenchConfig.read_raw(tmp_path)["plugin"] == {"custom_key": "custom-value"}


def test_authenticated_reload_can_save_without_resending_secrets(tmp_path: Path) -> None:
    client = setup_client(tmp_path)
    assert save_configuration(client).status_code == 200

    configuration = client.get("/api/v1/setup/configuration").get_json()
    response = client.put(
        "/api/v1/setup/configuration",
        json={"app_branch": "develop"},
    )

    assert configuration["admin_password_configured"] is True
    assert configuration["mariadb_password_configured"] is True
    assert "admin_password" not in configuration
    assert "mariadb_password" not in configuration
    assert response.status_code == 200
    config = BenchConfig.read(tmp_path)
    assert config.admin.password == "admin-secret"
    assert config.mariadb.root_password == "database-secret"


def test_configuration_update_rejects_malformed_and_invalid_payloads(
    tmp_path: Path,
) -> None:
    client = setup_client(tmp_path)

    malformed = client.put("/api/v1/setup/configuration", json=[])
    invalid = client.put(
        "/api/v1/setup/configuration",
        json={"admin_password": "secret", "mariadb_password": 123},
    )

    assert malformed.status_code == 400
    assert malformed.get_json()["error"]["code"] == "malformed_request"
    assert invalid.status_code == 422
    assert invalid.get_json()["error"]["code"] == "invalid_setup_configuration"


def test_only_one_unauthenticated_request_can_claim_setup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    first_in_write = threading.Event()
    second_passed_guard = threading.Event()
    release_first = threading.Event()
    original_write = BenchConfig.write_flat.__func__

    def delayed_write(*args, **kwargs):
        first_in_write.set()
        assert release_first.wait(2)
        return original_write(BenchConfig, *args, **kwargs)

    @app.before_request
    def observe_second_request():
        from flask import request

        if request.headers.get("X-Setup-Claim") == "second":
            second_passed_guard.set()

    monkeypatch.setattr(BenchConfig, "write_flat", delayed_write)
    responses = {}

    def claim(name: str) -> None:
        responses[name] = app.test_client().put(
            "/api/v1/setup/configuration",
            headers={"X-Setup-Claim": name},
            json={
                "admin_password": f"{name}-admin-secret",
                "mariadb_password": "database-secret",
            },
        )

    first = threading.Thread(target=claim, args=("first",))
    second = threading.Thread(target=claim, args=("second",))
    first.start()
    assert first_in_write.wait(2)
    second.start()
    assert second_passed_guard.wait(2)
    release_first.set()
    first.join(2)
    second.join(2)

    assert responses["first"].status_code == 200
    assert responses["second"].status_code == 401
    assert BenchConfig.read(tmp_path).admin.password == "first-admin-secret"


def test_database_validation_uses_one_engine_neutral_resource(tmp_path: Path) -> None:
    client = setup_client(tmp_path)
    with patch("pilot.managers.database.MariaDBManager") as manager_class:
        manager_class.return_value.is_installed.return_value = False
        response = client.post(
            "/api/v1/setup/database-validations",
            json={"engine": "mariadb", "password": "secret"},
        )

    assert response.status_code == 200
    assert response.get_json() == {"engine": "mariadb", "state": "will_install"}
    config = manager_class.call_args.args[0]
    assert config.root_password == "secret"
    assert config.admin_user == "root"
    assert config.port == 3306


def test_database_validation_supports_existing_postgres(tmp_path: Path) -> None:
    client = setup_client(tmp_path)
    with patch("pilot.managers.database.PostgresManager") as manager_class:
        manager_class.return_value.has_valid_credentials.return_value = False
        response = client.post(
            "/api/v1/setup/database-validations",
            json={
                "engine": "postgres",
                "password": "secret",
                "admin_user": "database-admin",
                "host": "db.example.com",
                "port": 5544,
                "existing": True,
            },
        )

    assert response.status_code == 200
    assert response.get_json() == {"engine": "postgres", "state": "invalid"}
    config = manager_class.call_args.args[0]
    assert config.admin_user == "database-admin"
    assert config.host == "db.example.com"
    assert config.port == 5544


def test_database_validation_rejects_invalid_engine_and_port(tmp_path: Path) -> None:
    client = setup_client(tmp_path)

    engine = client.post(
        "/api/v1/setup/database-validations",
        json={"engine": "sqlite"},
    )
    port = client.post(
        "/api/v1/setup/database-validations",
        json={"engine": "mariadb", "port": True},
    )

    assert engine.status_code == 422
    assert engine.get_json()["error"]["code"] == "invalid_database_configuration"
    assert port.status_code == 422
    assert port.get_json()["error"]["code"] == "invalid_database_configuration"


def test_start_returns_the_task_resource_and_reuses_active_setup(
    tmp_path: Path,
) -> None:
    client = setup_client(tmp_path)
    assert save_configuration(client).status_code == 200

    first = start_setup(client, "first-attempt")
    second = start_setup(client, "second-attempt")

    assert first.status_code == 202
    assert first.headers["Location"] == f"/api/v1/tasks/{first.get_json()['task_id']}"
    assert first.get_json()["command"] == "wizard-setup"
    assert first.get_json()["status"] == "queued"
    assert second.status_code == 202
    assert second.get_json()["task_id"] == first.get_json()["task_id"]
    assert (tmp_path / ".wizard-active").exists()


def test_start_reuses_successful_task_until_finish(tmp_path: Path) -> None:
    client = setup_client(tmp_path)
    assert save_configuration(client).status_code == 200
    task_id = start_setup(client, "first-attempt").get_json()["task_id"]
    complete_task(tmp_path, task_id)

    response = start_setup(client, "second-attempt")

    assert response.status_code == 202
    assert response.get_json()["task_id"] == task_id
    assert response.get_json()["status"] == "success"
    assert client.get("/api/v1/setup/configuration").get_json()["running_setup_task_id"] == task_id
    assert len(list((tmp_path / "tasks").glob("20*"))) == 1


def test_failed_setup_can_retry_without_resending_saved_secrets(tmp_path: Path) -> None:
    client = setup_client(tmp_path)
    assert save_configuration(client).status_code == 200
    first_task_id = start_setup(client, "first-attempt").get_json()["task_id"]
    fail_task(tmp_path, first_task_id)

    configuration = client.get("/api/v1/setup/configuration").get_json()
    saved = client.put(
        "/api/v1/setup/configuration",
        json={"app_branch": "develop"},
    )
    retried = start_setup(client, "second-attempt")

    assert configuration["admin_password_configured"] is True
    assert configuration["mariadb_password_configured"] is True
    assert saved.status_code == 200
    assert retried.status_code == 202
    assert retried.get_json()["task_id"] != first_task_id


def test_start_requires_an_idempotency_key(tmp_path: Path) -> None:
    client = setup_client(tmp_path)
    assert save_configuration(client).status_code == 200

    response = client.post("/api/v1/setup/actions/start")

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "idempotency_key_required"
    assert not (tmp_path / ".wizard-active").exists()


def test_finish_requires_the_setup_task_to_be_successful(tmp_path: Path) -> None:
    client = setup_client(tmp_path)
    assert save_configuration(client).status_code == 200
    task_id = start_setup(client).get_json()["task_id"]

    response = client.post(
        "/api/v1/setup/actions/finish",
        json={"task_id": task_id},
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "setup_not_complete"
    assert (tmp_path / ".wizard-active").exists()


def test_finish_clears_the_marker_without_signalling_managed_web_process(
    tmp_path: Path,
) -> None:
    client = setup_client(tmp_path)
    assert save_configuration(client).status_code == 200
    task_id = start_setup(client).get_json()["task_id"]
    complete_task(tmp_path, task_id)
    procfile = tmp_path / "config" / "Procfile"
    procfile.parent.mkdir()
    procfile.touch()
    python = tmp_path / "env" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()

    bootstrap = client.get("/api/v1/bootstrap")

    assert bootstrap.get_json()["mode"] == "setup"
    assert (tmp_path / ".wizard-active").read_text() == task_id

    with patch("os.kill") as kill:
        response = client.post(
            "/api/v1/setup/actions/finish",
            json={"task_id": task_id},
        )

    assert response.status_code == 204
    assert response.data == b""
    assert not (tmp_path / ".wizard-active").exists()
    kill.assert_not_called()


def test_finish_preserves_marker_when_bench_is_not_initialized(tmp_path: Path) -> None:
    client = setup_client(tmp_path)
    assert save_configuration(client).status_code == 200
    task_id = start_setup(client).get_json()["task_id"]
    complete_task(tmp_path, task_id)

    response = client.post(
        "/api/v1/setup/actions/finish",
        json={"task_id": task_id},
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "setup_not_initialized"
    assert (tmp_path / ".wizard-active").exists()


def test_finish_requires_the_marker_bound_task(tmp_path: Path) -> None:
    client = setup_client(tmp_path)
    assert save_configuration(client).status_code == 200
    task_id = start_setup(client).get_json()["task_id"]
    complete_task(tmp_path, task_id)
    procfile = tmp_path / "config" / "Procfile"
    procfile.parent.mkdir()
    procfile.touch()
    (tmp_path / ".wizard-active").write_text("20260715-120000-ffffff")

    response = client.post(
        "/api/v1/setup/actions/finish",
        json={"task_id": task_id},
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "setup_task_mismatch"
    assert (tmp_path / ".wizard-active").exists()
