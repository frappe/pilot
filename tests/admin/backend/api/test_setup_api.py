from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from admin.backend.app import create_app
from pilot.config import BenchConfig, MariaDBConfig
from pilot.internal.tasks.store import TaskStore
from pilot.managers.task.models import TaskStatus


def setup_client(bench_root: Path, *, authenticated: bool = True):
    """A wizard client for a freshly created bench: bench.toml with an admin password
    (written by `pilot new`), plus the session its setup link would mint."""
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    bench_root.mkdir(parents=True, exist_ok=True)
    if not BenchConfig.exists(bench_root):
        BenchConfig.write_flat(
            bench_root, bench_root.name, {"admin_enabled": True, "admin_password": "admin-secret"}
        )
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    if authenticated:
        client.set_cookie("sid", Session(Bench(bench_root)).issue_session_token()[0])
    return client


def save_configuration(client):
    return client.put(
        "/api/v1/setup/configuration",
        json={"mariadb_password": "database-secret"},
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
    assert response.get_json()["mariadb_password_configured"] is True
    assert response.get_json()["postgres_password_configured"] is False
    assert "mariadb_password" not in response.get_json()
    assert BenchConfig.read(tmp_path).mariadb.root_password == "database-secret"
    assert BenchConfig.read_raw(tmp_path)["plugin"] == {"custom_key": "custom-value"}


def test_reload_can_save_without_resending_secrets(tmp_path: Path) -> None:
    client = setup_client(tmp_path)
    assert save_configuration(client).status_code == 200

    configuration = client.get("/api/v1/setup/configuration").get_json()
    response = client.put(
        "/api/v1/setup/configuration",
        json={"app_branch": "develop"},
    )

    assert configuration["mariadb_password_configured"] is True
    assert "mariadb_password" not in configuration
    assert response.status_code == 200
    assert BenchConfig.read(tmp_path).mariadb.root_password == "database-secret"


def test_configuration_update_rejects_malformed_and_invalid_payloads(
    tmp_path: Path,
) -> None:
    client = setup_client(tmp_path)

    malformed = client.put("/api/v1/setup/configuration", json=[])
    invalid = client.put(
        "/api/v1/setup/configuration",
        json={"mariadb_password": 123},
    )

    assert malformed.status_code == 400
    assert malformed.get_json()["error"]["code"] == "malformed_request"
    assert invalid.status_code == 422
    assert invalid.get_json()["error"]["code"] == "invalid_setup_configuration"


def test_configuration_update_rejects_keys_the_wizard_does_not_own(tmp_path: Path) -> None:
    client = setup_client(tmp_path)

    response = client.put(
        "/api/v1/setup/configuration",
        json={
            "admin_password": "hijacked",
            "mariadb_password": "database-secret",
            "admin_jwks_url": "https://attacker.example.com/jwks.json",
            "admin_allow_bench_management": True,
        },
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_setup_configuration"
    assert "admin_jwks_url" in response.get_json()["error"]["message"]
    assert BenchConfig.read(tmp_path).admin.verify_password("admin-secret")


def test_setup_configuration_needs_a_session(tmp_path: Path) -> None:
    client = setup_client(tmp_path, authenticated=False)

    read = client.get("/api/v1/setup/configuration")
    write = client.put("/api/v1/setup/configuration", json={"mariadb_password": "database-secret"})

    assert read.status_code == 401
    assert write.status_code == 401
    assert write.get_json()["error"]["code"] == "authentication_required"


def _write_sibling_local_database(benches: Path, name: str, password: str) -> None:
    sibling = benches / name
    sibling.mkdir()
    BenchConfig.write_flat(
        sibling,
        name,
        {
            "mariadb_password": password,
            "mariadb_admin_user": "root",
            "mariadb_host": "localhost",
            "mariadb_port": 3306,
        },
    )


def test_local_database_available_reflects_a_sibling_benchs_local_server(
    tmp_path: Path,
) -> None:
    benches = tmp_path / "benches"
    benches.mkdir()
    _write_sibling_local_database(benches, "existing-bench", "sibling-secret")
    client = setup_client(benches / "new-bench")

    configuration = client.get("/api/v1/setup/configuration").get_json()

    assert configuration["mariadb_local_available"] is True
    assert configuration["postgres_local_available"] is False


def test_local_database_unavailable_without_a_configured_sibling(tmp_path: Path) -> None:
    benches = tmp_path / "benches"
    benches.mkdir()
    solo_bench = benches / "solo-bench"
    solo_bench.mkdir()
    client = setup_client(solo_bench)

    configuration = client.get("/api/v1/setup/configuration").get_json()

    assert configuration["mariadb_local_available"] is False
    assert configuration["postgres_local_available"] is False


def test_existing_local_database_mode_copies_sibling_credentials(tmp_path: Path) -> None:
    benches = tmp_path / "benches"
    benches.mkdir()
    _write_sibling_local_database(benches, "existing-bench", "sibling-secret")
    bench_root = benches / "new-bench"
    bench_root.mkdir()
    client = setup_client(bench_root)

    response = client.put(
        "/api/v1/setup/configuration",
        json={"db_type": "mariadb", "db_mode": "existing_local"},
    )

    assert response.status_code == 200
    assert "mariadb_password" not in response.get_json()
    config = BenchConfig.read(bench_root)
    assert config.mariadb.root_password == "sibling-secret"
    assert config.mariadb.existing is False
    assert config.mariadb.host == "localhost"


def test_existing_local_database_mode_without_a_sibling_still_requires_a_password(
    tmp_path: Path,
) -> None:
    benches = tmp_path / "benches"
    benches.mkdir()
    solo_bench = benches / "solo-bench"
    solo_bench.mkdir()
    client = setup_client(solo_bench)

    response = client.put(
        "/api/v1/setup/configuration",
        json={"db_type": "mariadb", "db_mode": "existing_local"},
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_setup_configuration"


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
    assert config.port == MariaDBConfig().port


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
    python = tmp_path / "env" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    (tmp_path / ".wizard-active").write_text("20260715-120000-ffffff")

    response = client.post(
        "/api/v1/setup/actions/finish",
        json={"task_id": task_id},
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "setup_task_mismatch"
    assert (tmp_path / ".wizard-active").exists()


def test_finish_is_retryable_after_the_marker_was_already_cleared(tmp_path: Path) -> None:
    """A client can lose the response to a successful finish() and retry -
    that retry must still succeed rather than 409 just because the marker
    is already gone."""
    client = setup_client(tmp_path)
    assert save_configuration(client).status_code == 200
    task_id = start_setup(client).get_json()["task_id"]
    complete_task(tmp_path, task_id)
    python = tmp_path / "env" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    first = client.post("/api/v1/setup/actions/finish", json={"task_id": task_id})
    assert first.status_code == 204
    assert not (tmp_path / ".wizard-active").exists()

    retry = client.post("/api/v1/setup/actions/finish", json={"task_id": task_id})

    assert retry.status_code == 204
    assert not (tmp_path / ".wizard-active").exists()
