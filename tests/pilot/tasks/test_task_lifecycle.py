from __future__ import annotations

import json
import os
import signal
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import pilot.tasks.manager.task_runner as task_runner_module
import pilot.tasks.manager.wrapper as wrapper_module
import pilot.tasks.callbacks as callback_module
from pilot.tasks.manager.task_reader import TaskReader
from pilot.tasks.manager.task_runner import TaskRunner
from pilot.tasks.manager.wrapper import callback_handler, run_with_syslog_output
from pilot.exceptions import TaskConflictError, TaskNotFoundError, TaskNotRunningError


TASK_ID = "20260715-120000-aabbcc"


def successful_callback(meta: dict, args: dict) -> None:
    return None


def write_success_marker(meta: dict, args: dict) -> None:
    (Path(meta["bench_root"]) / "success.marker").write_text("")


def write_failure_marker(meta: dict, args: dict) -> None:
    (Path(meta["bench_root"]) / "failure.marker").write_text("")


def failing_callback(meta: dict, args: dict) -> None:
    raise RuntimeError("callback error")


def task_meta(bench_root: Path) -> dict:
    return {
        "task_id": TASK_ID,
        "command": "build",
        "args": {},
        "command_argv": [sys.executable, "-c", "print('done')"],
        "queued_at": "2026-07-15T11:59:59+00:00",
        "started_at": "2026-07-15T12:00:00+00:00",
        "finished_at": None,
        "exit_code": None,
        "failure": None,
        "bench_root": str(bench_root),
    }


def test_run_persists_task_before_starting_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID

    def wake_worker(bench_root: Path):
        assert bench_root == tmp_path
        assert (task_dir / "meta.json").exists()
        assert (task_dir / "status").read_text() == "queued"
        assert (task_dir / "callbacks.json").exists()
        assert not (task_dir / "pid").exists()
        return True

    monkeypatch.setattr(TaskRunner, "_generate_task_id", staticmethod(lambda: TASK_ID))
    monkeypatch.setattr(task_runner_module.task_workers, "wake", wake_worker)
    monkeypatch.setitem(callback_module._OPERATIONS, "test-success", successful_callback)

    task_id = TaskRunner(tmp_path).run(
        "build",
        {},
        callbacks={
            "on_success": {"operation": "test-success", "args": {"marker": "success"}},
            "on_failure": None,
        },
    )

    meta = json.loads((task_dir / "meta.json").read_text())
    assert task_id == TASK_ID
    assert set(meta) == {
        "args",
        "bench_root",
        "command",
        "command_argv",
        "exit_code",
        "failure",
        "finished_at",
        "queued_at",
        "queue_sequence",
        "started_at",
        "task_id",
    }
    assert meta["task_id"] == TASK_ID
    assert meta["command"] == "build"
    assert meta["args"] == {}
    assert meta["bench_root"] == str(tmp_path)
    assert meta["queued_at"] is not None
    assert meta["queue_sequence"] == 1
    assert meta["started_at"] is None
    assert meta["finished_at"] is None
    assert meta["exit_code"] is None
    assert meta["failure"] is None
    assert not (task_dir / "pid").exists()
    assert json.loads((task_dir / "callbacks.json").read_text()) == {
        "on_success": {"operation": "test-success", "args": {"marker": "success"}}
    }
    assert stat.S_IMODE((tmp_path / "tasks").stat().st_mode) == 0o700
    assert stat.S_IMODE(task_dir.stat().st_mode) == 0o700
    for name in ("meta.json", "status", "callbacks.json"):
        assert stat.S_IMODE((task_dir / name).stat().st_mode) == 0o600


def test_run_rejects_unknown_callback_operation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown callback operation"):
        TaskRunner(tmp_path).run(
            "build",
            {},
            callbacks={
                "on_success": {"operation": "import-anything", "args": {}},
                "on_failure": None,
            },
        )


def test_run_reuses_active_task_for_same_idempotency_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_ids = iter([TASK_ID, "20260715-120001-bbccdd"])
    started = []

    def wake_worker(bench_root: Path):
        started.append(bench_root)
        return True

    monkeypatch.setattr(TaskRunner, "_generate_task_id", staticmethod(lambda: next(task_ids)))
    monkeypatch.setattr(task_runner_module.task_workers, "wake", wake_worker)
    runner = TaskRunner(tmp_path)

    first = runner.run("build", {}, idempotency_key="client-request-key")
    duplicate = runner.run("build", {}, idempotency_key="client-request-key")

    metadata_text = (tmp_path / "tasks" / TASK_ID / "meta.json").read_text()
    assert duplicate == first == TASK_ID
    assert len(started) == 1
    assert "client-request-key" not in metadata_text
    assert "idempotency_digest" in metadata_text


def test_submit_reports_new_and_replayed_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_ids = iter([TASK_ID, "20260715-120001-bbccdd"])
    monkeypatch.setattr(TaskRunner, "_generate_task_id", staticmethod(lambda: next(task_ids)))
    monkeypatch.setattr(task_runner_module.task_workers, "wake", lambda bench_root: True)
    runner = TaskRunner(tmp_path)

    first = runner.submit("build", {}, idempotency_key="client-request-key")
    replay = runner.submit("build", {}, idempotency_key="client-request-key")

    assert first.task_id == replay.task_id == TASK_ID
    assert first.created is True
    assert replay.created is False


def test_submit_returns_published_task_when_housekeeping_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    housekeeping = []

    def fail_wake(bench_root: Path) -> None:
        housekeeping.append(("wake", bench_root))
        raise RuntimeError("wake failed")

    def fail_purge(limit: int) -> None:
        housekeeping.append(("purge", limit))
        raise OSError("purge failed")

    monkeypatch.setattr(TaskRunner, "_generate_task_id", staticmethod(lambda: TASK_ID))
    monkeypatch.setattr(task_runner_module.task_workers, "wake", fail_wake)
    runner = TaskRunner(tmp_path)
    monkeypatch.setattr(runner._store, "purge_terminal", fail_purge)

    submission = runner.submit("build", {})

    assert submission.task_id == TASK_ID
    assert submission.created is True
    assert (tmp_path / "tasks" / TASK_ID / "meta.json").exists()
    assert housekeeping == [
        ("wake", tmp_path),
        ("purge", task_runner_module.TASK_RETENTION_LIMIT),
    ]


def test_run_rejects_idempotency_key_reuse_for_different_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_ids = iter([TASK_ID, "20260715-120001-bbccdd"])
    monkeypatch.setattr(TaskRunner, "_generate_task_id", staticmethod(lambda: next(task_ids)))
    monkeypatch.setattr(task_runner_module.task_workers, "wake", lambda bench_root: True)
    runner = TaskRunner(tmp_path)
    runner.run("build", {}, idempotency_key="client-request-key")

    with pytest.raises(TaskConflictError, match="another active task"):
        runner.run(
            "build",
            {"app": "frappe"},
            idempotency_key="client-request-key",
        )


def test_run_rejects_unknown_callback_trigger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(callback_module._OPERATIONS, "test-success", successful_callback)

    with pytest.raises(ValueError, match="Unknown callback trigger"):
        TaskRunner(tmp_path).run(
            "build",
            {},
            callbacks={"always": {"operation": "test-success", "args": {}}},
        )


def test_run_hands_secret_to_job_without_persisting_it_publicly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    password = "unique-admin-password"

    monkeypatch.setattr(TaskRunner, "_generate_task_id", staticmethod(lambda: TASK_ID))
    monkeypatch.setattr(task_runner_module.task_workers, "wake", lambda bench_root: True)

    TaskRunner(tmp_path).run(
        "new-site",
        {"name": "new.localhost", "admin_password": password},
    )

    meta_text = (task_dir / "meta.json").read_text()
    meta = json.loads(meta_text)
    secret_path = task_dir / "secrets.json"
    assert password not in meta_text
    assert password not in "\0".join(meta["command_argv"])
    assert "--admin-password" not in meta["command_argv"]
    assert meta["args"]["admin_password"] == "[redacted]"
    assert json.loads(secret_path.read_text()) == {"admin_password": password}
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600


def test_restore_task_hides_upload_paths_from_public_args(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_dir = tmp_path / "tmp" / "uploads" / "request"
    upload_dir.mkdir(parents=True)
    database = upload_dir / "database.sql.gz"
    public_files = upload_dir / "public.tar"
    private_files = upload_dir / "private.tar"
    for path in (database, public_files, private_files):
        path.write_bytes(path.name.encode())
    monkeypatch.setattr(TaskRunner, "_generate_task_id", staticmethod(lambda: TASK_ID))
    monkeypatch.setattr(task_runner_module.task_workers, "wake", lambda bench_root: True)

    TaskRunner(tmp_path).run(
        "new-site-from-backup",
        {
            "name": "example.test",
            "admin_password": "secret",
            "db_file": str(database),
            "public_files": str(public_files),
            "private_files": str(private_files),
        },
    )

    public_args = json.loads(
        (tmp_path / "tasks" / TASK_ID / "meta.json").read_text()
    )["args"]
    assert public_args == {
        "name": "example.test",
        "admin_password": "[redacted]",
    }


def test_restore_idempotency_fingerprints_upload_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_ids = iter([TASK_ID, "20260715-120001-bbccdd"])
    first_upload = tmp_path / "tmp" / "uploads" / "first" / "database.sql.gz"
    second_upload = tmp_path / "tmp" / "uploads" / "second" / "database.sql.gz"
    first_upload.parent.mkdir(parents=True)
    second_upload.parent.mkdir(parents=True)
    first_upload.write_bytes(b"same database backup")
    second_upload.write_bytes(b"same database backup")
    monkeypatch.setattr(TaskRunner, "_generate_task_id", staticmethod(lambda: next(task_ids)))
    monkeypatch.setattr(task_runner_module.task_workers, "wake", lambda bench_root: True)
    runner = TaskRunner(tmp_path)

    first = runner.submit(
        "new-site-from-backup",
        {
            "name": "example.test",
            "admin_password": "secret",
            "db_file": str(first_upload),
        },
        idempotency_key="restore-request",
    )
    replay = runner.submit(
        "new-site-from-backup",
        {
            "name": "example.test",
            "admin_password": "secret",
            "db_file": str(second_upload),
        },
        idempotency_key="restore-request",
    )

    assert first.created is True
    assert replay.created is False
    assert replay.task_id == first.task_id


def test_restore_idempotency_rejects_different_upload_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_ids = iter([TASK_ID, "20260715-120001-bbccdd"])
    first_upload = tmp_path / "first.sql.gz"
    second_upload = tmp_path / "second.sql.gz"
    first_upload.write_bytes(b"first backup")
    second_upload.write_bytes(b"second backup")
    monkeypatch.setattr(TaskRunner, "_generate_task_id", staticmethod(lambda: next(task_ids)))
    monkeypatch.setattr(task_runner_module.task_workers, "wake", lambda bench_root: True)
    runner = TaskRunner(tmp_path)
    common_args = {"name": "example.test", "admin_password": "secret"}

    runner.run(
        "new-site-from-backup",
        {**common_args, "db_file": str(first_upload)},
        idempotency_key="restore-request",
    )

    with pytest.raises(TaskConflictError, match="another active task"):
        runner.run(
            "new-site-from-backup",
            {**common_args, "db_file": str(second_upload)},
            idempotency_key="restore-request",
        )


def test_base_task_loads_secret_arguments_from_handoff_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pilot.tasks.jobs.base_task import _apply_task_secrets

    secret_path = tmp_path / "secrets.json"
    secret_path.write_text(json.dumps({"admin_password": "from-file"}))
    monkeypatch.setenv("BENCH_TASK_SECRETS_FILE", str(secret_path))
    args = SimpleNamespace(admin_password=None)

    _apply_task_secrets(args)

    assert args.admin_password == "from-file"


@pytest.mark.parametrize(
    "status", ["queued", "running", "success", "failed", "killed"]
)
def test_task_reader_preserves_current_statuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "meta.json").write_text(json.dumps(task_meta(tmp_path)))
    (task_dir / "status").write_text(status)
    (task_dir / "pid").write_text("4321")
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    task = TaskReader(tmp_path).read_task(TASK_ID)

    assert task.status == status
    assert task.pid == 4321


def test_task_reader_redacts_secrets_in_legacy_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    meta = task_meta(tmp_path)
    meta["args"] = {"name": "new.localhost", "admin_password": "legacy-password"}
    (task_dir / "meta.json").write_text(json.dumps(meta))
    (task_dir / "status").write_text("success")
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    task = TaskReader(tmp_path).read_task(TASK_ID)

    assert task.args == {"name": "new.localhost", "admin_password": "[redacted]"}


def test_kill_cancels_queued_task_without_pid(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "meta.json").write_text(json.dumps(task_meta(tmp_path)))
    (task_dir / "status").write_text("queued")

    TaskRunner(tmp_path).kill(TASK_ID)

    assert (task_dir / "status").read_text() == "killed"


def test_kill_rejects_missing_task(tmp_path: Path) -> None:
    with pytest.raises(TaskNotFoundError, match=TASK_ID):
        TaskRunner(tmp_path).kill(TASK_ID)


def test_kill_rejects_completed_task(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "status").write_text("success")

    with pytest.raises(TaskNotRunningError, match="status=success"):
        TaskRunner(tmp_path).kill(TASK_ID)


def test_wrapper_output_is_readable_without_syslog_envelopes(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    output_path = task_dir / "output.log"
    command = [
        sys.executable,
        "-c",
        "import sys; print('standard output', flush=True); print('standard error', file=sys.stderr, flush=True)",
    ]

    exit_code = run_with_syslog_output(command, str(tmp_path), "build", output_path)

    meta = task_meta(tmp_path)
    meta["exit_code"] = exit_code
    meta["finished_at"] = "2026-07-15T12:00:01+00:00"
    (task_dir / "meta.json").write_text(json.dumps(meta))
    (task_dir / "status").write_text("success")
    assert exit_code == 0
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
    assert TaskReader(tmp_path).read_output(TASK_ID) == ["standard output", "standard error"]


def test_wrapper_redacts_secrets_from_task_output(tmp_path: Path) -> None:
    output_path = tmp_path / "output.log"
    password = "do-not-log-this-password"
    command = [sys.executable, "-c", f"print({password!r})"]

    exit_code = run_with_syslog_output(
        command,
        str(tmp_path),
        "new-site",
        output_path,
        redactions=[password],
    )

    output = output_path.read_text()
    assert exit_code == 0
    assert password not in output
    assert "[redacted]" in output


def test_wrapper_loads_config_redactions_and_removes_secret_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "meta.json").write_text(json.dumps(task_meta(tmp_path)))
    (task_dir / "status").write_text("running")
    (task_dir / "secrets.json").write_text(
        json.dumps({"admin_password": "task-password"})
    )
    (tmp_path / "bench.toml").write_text(
        '[mariadb]\nroot_password = "database-password"\n'
    )
    captured = {}

    def run_task(*args):
        captured["redactions"] = args[4]
        return 0

    monkeypatch.setattr(wrapper_module, "run_with_syslog_output", run_task)
    monkeypatch.setattr(sys, "argv", ["wrapper", str(task_dir)])

    wrapper_module.main()

    assert set(captured["redactions"]) >= {"task-password", "database-password"}
    assert not (task_dir / "secrets.json").exists()


def test_wrapper_does_not_claim_queued_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "meta.json").write_text(json.dumps(task_meta(tmp_path)))
    (task_dir / "status").write_text("queued")
    monkeypatch.setattr(
        wrapper_module,
        "run_with_syslog_output",
        lambda *args: pytest.fail("queued task was executed"),
    )
    monkeypatch.setattr(sys, "argv", ["wrapper", str(task_dir)])

    wrapper_module.main()

    assert (task_dir / "status").read_text() == "queued"


@pytest.mark.parametrize(
    ("exit_code", "status", "marker", "other_marker"),
    [
        (0, "success", "success.marker", "failure.marker"),
        (1, "failed", "failure.marker", "success.marker"),
    ],
)
def test_wrapper_runs_matching_callback_and_finalizes_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    status: str,
    marker: str,
    other_marker: str,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "meta.json").write_text(json.dumps(task_meta(tmp_path)))
    (task_dir / "status").write_text("running")
    (task_dir / "callbacks.json").write_text(
        json.dumps(
            {
                "on_success": {"operation": "test-success", "args": {}},
                "on_failure": {"operation": "test-failure", "args": {}},
            }
        )
    )
    monkeypatch.setitem(callback_module._OPERATIONS, "test-success", write_success_marker)
    monkeypatch.setitem(callback_module._OPERATIONS, "test-failure", write_failure_marker)
    monkeypatch.setattr(wrapper_module, "run_with_syslog_output", lambda *args: exit_code)
    monkeypatch.setattr(sys, "argv", ["wrapper", str(task_dir)])

    wrapper_module.main()

    final_meta = json.loads((task_dir / "meta.json").read_text())
    assert (tmp_path / marker).exists()
    assert not (tmp_path / other_marker).exists()
    assert not (task_dir / "callbacks.json").exists()
    assert (task_dir / "status").read_text() == status
    assert final_meta["exit_code"] == exit_code
    assert final_meta["finished_at"] is not None
    assert final_meta["failure"] == (
        None if exit_code == 0 else {"code": "command_failed"}
    )
    assert "Callback successfully triggered" in (task_dir / "output.log").read_text()


def test_wrapper_runs_only_cancel_callback_after_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "meta.json").write_text(json.dumps(task_meta(tmp_path)))
    (task_dir / "status").write_text("running")
    (task_dir / "callbacks.json").write_text(
        json.dumps(
            {
                "on_success": {"operation": "test-success", "args": {}},
                "on_failure": {"operation": "test-failure", "args": {}},
                "on_cancel": {"operation": "test-cancel", "args": {}},
            }
        )
    )
    monkeypatch.setitem(callback_module._OPERATIONS, "test-success", write_success_marker)
    monkeypatch.setitem(callback_module._OPERATIONS, "test-failure", write_failure_marker)
    monkeypatch.setitem(callback_module._OPERATIONS, "test-cancel", write_success_marker)

    def cancel_during_task(*args) -> int:
        (task_dir / "status").write_text("killed")
        wrapper_module._request_cancel(None, None)
        return -signal.SIGTERM

    monkeypatch.setattr(wrapper_module, "run_with_syslog_output", cancel_during_task)
    monkeypatch.setattr(sys, "argv", ["wrapper", str(task_dir)])

    wrapper_module.main()

    assert (task_dir / "status").read_text() == "killed"
    assert not (task_dir / "callbacks.json").exists()
    assert (tmp_path / "success.marker").exists()
    assert not (tmp_path / "failure.marker").exists()


def test_wrapper_terminal_state_wins_before_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "meta.json").write_text(json.dumps(task_meta(tmp_path)))
    (task_dir / "status").write_text("running")
    (task_dir / "callbacks.json").write_text(
        json.dumps(
            {"on_success": {"operation": "test-late-cancel", "args": {}}}
        )
    )
    observed = []

    def attempt_late_cancel(meta: dict, args: dict) -> None:
        observed.append((task_dir / "status").read_text())
        with pytest.raises(TaskNotRunningError, match="status=success"):
            TaskRunner(tmp_path).kill(TASK_ID)

    monkeypatch.setitem(
        callback_module._OPERATIONS,
        "test-late-cancel",
        attempt_late_cancel,
    )
    monkeypatch.setattr(wrapper_module, "run_with_syslog_output", lambda *args: 0)
    monkeypatch.setattr(sys, "argv", ["wrapper", str(task_dir)])

    wrapper_module.main()

    assert observed == ["success"]
    assert (task_dir / "status").read_text() == "success"
    assert not (task_dir / "callbacks.json").exists()


def test_callback_failure_is_logged_and_callback_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "output.log"
    monkeypatch.setitem(callback_module._OPERATIONS, "test-failing", failing_callback)

    callback_handler(
        {"operation": "test-failing", "args": {}},
        output_path,
        task_meta(tmp_path),
    )

    assert "Callback failed: callback error" in output_path.read_text()


def test_callback_handler_rejects_tampered_operation_id(tmp_path: Path) -> None:
    output_path = tmp_path / "output.log"

    callback_handler(
        {"operation": "os.system", "args": {"command": "touch escaped"}},
        output_path,
        task_meta(tmp_path),
    )

    assert "Unknown callback operation" in output_path.read_text()
    assert not (tmp_path / "escaped").exists()


def test_wrapper_deletes_legacy_pickle_without_loading_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_dir = tmp_path / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "meta.json").write_text(json.dumps(task_meta(tmp_path)))
    (task_dir / "status").write_text("running")
    legacy_path = task_dir / "on_success.bin"
    legacy_path.write_bytes(b"not-even-a-valid-pickle")
    monkeypatch.setattr(wrapper_module, "run_with_syslog_output", lambda *args: 0)
    monkeypatch.setattr(sys, "argv", ["wrapper", str(task_dir)])

    wrapper_module.main()

    assert not legacy_path.exists()
