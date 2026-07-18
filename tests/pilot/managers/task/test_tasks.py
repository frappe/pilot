"""Tests for pilot.tasks — TaskRunner and TaskReader."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import pilot.managers.task.reader as task_reader_module
from pilot.managers.task.models import TaskStatus
import pilot.internal.tasks.runner as task_runner_module
from pilot.internal.tasks.store import TaskStore
from pilot.internal.tasks.runner import TASK_RETENTION_LIMIT
from pilot.managers.task.reader import TaskReader, collapse_cr
from pilot.tasks import TaskRunner


def test_generate_task_id_format() -> None:
    task_id = task_runner_module.generate_task_id()
    assert re.match(r"^\d{8}-\d{6}-[a-f0-9]{6}$", task_id), f"Unexpected format: {task_id!r}"


def task_argv(tmp_path: Path, command: str, args: dict) -> list[str]:
    task_id = TaskRunner(tmp_path).run(command, args)
    meta = json.loads((tmp_path / "tasks" / task_id / "meta.json").read_text())
    return meta["command_argv"]


def test_command_argv_migrate(tmp_path: Path) -> None:
    argv = task_argv(tmp_path, "migrate", {"site": "mysite.localhost"})
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pilot.tasks.migrate"]
    assert str(tmp_path) in argv
    assert "mysite.localhost" in argv


def test_command_argv_clear_cache(tmp_path: Path) -> None:
    argv = task_argv(tmp_path, "clear-cache", {"site": "mysite.localhost"})
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pilot.tasks.clear_cache"]
    assert str(tmp_path) in argv
    assert "mysite.localhost" in argv


def test_command_argv_setup_letsencrypt_carries_site_and_email(tmp_path: Path) -> None:
    argv = task_argv(
        tmp_path,
        "setup-letsencrypt",
        {"site": "mysite.localhost", "email": "ops@example.com"},
    )

    assert argv[1:3] == ["-m", "pilot.tasks.setup_letsencrypt"]
    assert argv[-4:] == ["--site", "mysite.localhost", "--email", "ops@example.com"]


def test_command_argv_install_app(tmp_path: Path) -> None:
    argv = task_argv(tmp_path, "install-app", {"site": "mysite.localhost", "app": "erpnext"})
    # install-app uses the install_app_task module (chains install + build)
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pilot.tasks.install_app"]
    assert str(tmp_path) in argv
    assert "mysite.localhost" in argv
    assert "erpnext" in argv


def test_command_argv_uninstall_app(tmp_path: Path) -> None:
    argv = task_argv(
        tmp_path,
        "uninstall-app",
        {"site": "mysite.localhost", "app": "erpnext"},
    )
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pilot.tasks.uninstall_app"]
    assert str(tmp_path) in argv
    assert "mysite.localhost" in argv
    assert "erpnext" in argv


def test_command_argv_new_site_carries_no_db_type(tmp_path: Path) -> None:
    # The engine is a bench-level setting now, so site tasks never pass --db-type.
    argv = task_argv(tmp_path, "new-site", {"name": "site1.localhost", "admin_password": "x"})
    assert argv[1:3] == ["-m", "pilot.tasks.new_site"]
    assert "site1.localhost" in argv
    assert "--db-type" not in argv


@pytest.mark.parametrize(
    "command,args",
    [
        ("new-site", {"name": "site.localhost"}),
        ("new-site-from-backup", {"name": "site.localhost", "db_file": "/tmp/db.sql"}),
        ("reinstall-site", {"site": "site.localhost"}),
    ],
)
def test_site_tasks_require_admin_password(tmp_path: Path, command: str, args: dict) -> None:
    with pytest.raises(ValueError, match="admin_password"):
        TaskRunner(tmp_path).run(command, args)


@pytest.mark.parametrize("password", ["", "   ", None])
def test_site_tasks_reject_empty_admin_password(tmp_path: Path, password) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        TaskRunner(tmp_path).run("new-site", {"name": "site.localhost", "admin_password": password})


def test_command_argv_get_app(tmp_path: Path) -> None:
    argv = task_argv(
        tmp_path, "get-app", {"name": "erpnext", "repo": "https://github.com/frappe/erpnext"}
    )
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pilot.tasks.get_app"]
    assert str(tmp_path) in argv
    assert "https://github.com/frappe/erpnext" in argv


def test_command_argv_get_app_with_branch(tmp_path: Path) -> None:
    argv = task_argv(
        tmp_path,
        "get-app",
        {"name": "erpnext", "repo": "https://github.com/frappe/erpnext", "branch": "version-16"},
    )
    assert "--branch" in argv
    assert "version-16" in argv


def test_command_argv_get_and_install_app_accepts_site_alias(tmp_path: Path) -> None:
    argv = task_argv(
        tmp_path,
        "get-and-install-app",
        {
            "site": "mysite.localhost",
            "repo": "https://github.com/frappe/helpdesk",
        },
    )

    assert argv[1:3] == ["-m", "pilot.tasks.get_and_install_app"]
    assert "--site" in argv
    assert "mysite.localhost" in argv
    assert "--sites" not in argv


def test_command_argv_rejects_credentials_in_repo_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Git provider connection"):
        TaskRunner(tmp_path).run(
            "get-app",
            {"name": "private", "repo": "https://token@github.com/acme/private.git"},
        )


def test_command_argv_build_no_app(tmp_path: Path) -> None:
    argv = task_argv(tmp_path, "build", {})
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pilot.tasks.build"]
    assert str(tmp_path) in argv
    assert "--app" not in argv


def test_command_argv_build_with_app(tmp_path: Path) -> None:
    argv = task_argv(tmp_path, "build", {"app": "erpnext"})
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pilot.tasks.build"]
    assert "--app" in argv
    assert "erpnext" in argv


def test_command_argv_update(tmp_path: Path) -> None:
    argv = task_argv(tmp_path, "update", {})
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pilot.tasks.update"]
    assert str(tmp_path) in argv


def test_command_argv_update_skip_failing_patches(tmp_path: Path) -> None:
    argv = task_argv(tmp_path, "update", {"skip_failing_patches": True})
    assert "--skip-failing-patches" in argv


def test_command_argv_update_without_skip_failing_patches(tmp_path: Path) -> None:
    argv = task_argv(tmp_path, "update", {"skip_failing_patches": False})
    assert "--skip-failing-patches" not in argv


def test_command_argv_switch_branch(tmp_path: Path) -> None:
    argv = task_argv(tmp_path, "switch-branch", {"name": "gameplan", "branch": "develop"})
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pilot.tasks.switch_branch"]
    assert str(tmp_path) in argv
    assert "gameplan" in argv
    assert "develop" in argv


def test_command_argv_backup_site(tmp_path: Path) -> None:
    argv = task_argv(tmp_path, "backup-site", {"site": "mysite.localhost"})
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pilot.tasks.backup_site"]
    assert str(tmp_path) in argv
    assert "mysite.localhost" in argv
    assert "--with-files" not in argv


def test_command_argv_unknown_command_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown command"):
        TaskRunner(tmp_path).run("hack-the-system", {})


def test_command_argv_missing_site_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="site"):
        TaskRunner(tmp_path).run("migrate", {})


def test_command_argv_install_app_requires_app(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="app"):
        TaskRunner(tmp_path).run("install-app", {"site": "mysite.localhost"})


def test_command_argv_switch_branch_requires_name_and_branch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="name"):
        TaskRunner(tmp_path).run("switch-branch", {"branch": "develop"})
    with pytest.raises(ValueError, match="branch"):
        TaskRunner(tmp_path).run("switch-branch", {"name": "gameplan"})


def _make_task_dir(tasks_root: Path, task_id: str, status: str = "success") -> Path:
    """Helper: create a minimal on-disk task directory."""
    task_dir = tasks_root / task_id
    task_dir.mkdir(parents=True)
    meta = {
        "task_id": task_id,
        "command": "build",
        "args": {},
        "command_argv": ["/usr/bin/bench", "frappe", "build"],
        "started_at": "2026-05-21T14:30:22+00:00",
        "finished_at": "2026-05-21T14:30:35+00:00",
        "exit_code": 0,
    }
    (task_dir / "meta.json").write_text(json.dumps(meta))
    (task_dir / "status").write_text(status)
    (task_dir / "pid").write_text("12345")
    (task_dir / "output.log").write_text("")
    return task_dir


def test_read_output_returns_last_n_lines(tmp_path: Path) -> None:
    task_id = "20260521-143022-aabbcc"
    task_dir = _make_task_dir(tmp_path / "tasks", task_id)
    lines = [f"line {i}" for i in range(1, 301)]
    (task_dir / "output.log").write_text("\n".join(lines))

    reader = TaskReader(tmp_path)
    with patch("os.kill", return_value=None):
        result = reader.read_output(task_id, lines=50)

    assert len(result) == 50
    assert result[0] == "line 251"
    assert result[-1] == "line 300"


def test_read_output_returns_all_lines_when_fewer_than_limit(tmp_path: Path) -> None:
    task_id = "20260521-143022-aabbcc"
    task_dir = _make_task_dir(tmp_path / "tasks", task_id)
    (task_dir / "output.log").write_text("alpha\nbeta\ngamma")

    reader = TaskReader(tmp_path)
    with patch("os.kill", return_value=None):
        result = reader.read_output(task_id, lines=200)

    assert result == ["alpha", "beta", "gamma"]


def test_iter_output_streams_display_text_without_syslog_envelopes(tmp_path: Path) -> None:
    task_id = "20260521-143022-aabbcc"
    task_dir = _make_task_dir(tmp_path / "tasks", task_id)
    envelope = "<14>1 2026-07-15T12:00:00Z host build 1 - - "
    (task_dir / "output.log").write_text(f"{envelope}started\n{envelope}[50%]\r{envelope}[70%]\n")

    output = "".join(TaskReader(tmp_path).iter_output(task_id))

    assert output == "started\n[70%]\n"


def test_stream_output_yields_structured_events(tmp_path: Path) -> None:
    task_id = "20260521-143022-aabbcc"
    task_dir = _make_task_dir(tmp_path / "tasks", task_id)
    (task_dir / "output.log").write_text("alpha\nbeta\n")

    events = list(TaskReader(tmp_path).stream_output(task_id))

    assert events == [
        {"type": "status", "status": "success", "queue_position": None},
        {"type": "line", "line": "alpha"},
        {"type": "line", "line": "beta"},
        {
            "type": "done",
            "status": "success",
            "exit_code": 0,
            "failure": None,
        },
    ]


def test_stream_waits_across_queued_running_and_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "20260521-143022-aabbcc"
    store = TaskStore(tmp_path)
    store.create_queued(
        {
            "task_id": task_id,
            "command": "build",
            "args": {},
            "queued_at": "2026-05-21T14:30:22+00:00",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "failure": None,
        }
    )
    polls = 0

    def advance_task(_interval: float) -> None:
        nonlocal polls
        polls += 1
        if polls == 1:
            store.transition(
                task_id,
                TaskStatus.QUEUED,
                TaskStatus.RUNNING,
                {"started_at": "2026-05-21T14:30:23+00:00"},
            )
        elif polls == 2:
            store.transition(
                task_id,
                TaskStatus.RUNNING,
                TaskStatus.SUCCESS,
                {
                    "finished_at": "2026-05-21T14:30:24+00:00",
                    "exit_code": 0,
                },
            )

    monkeypatch.setattr(task_reader_module.time, "sleep", advance_task)

    events = list(TaskReader(tmp_path).stream_output(task_id))

    assert [event["type"] for event in events] == ["status", "status", "status", "done"]
    assert [event["status"] for event in events] == [
        "queued",
        "running",
        "success",
        "success",
    ]
    assert events[-1]["exit_code"] == 0


def test_stream_waits_for_queued_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "20260521-143022-aabbcc"
    store = TaskStore(tmp_path)
    store.create_queued(
        {
            "task_id": task_id,
            "command": "build",
            "args": {},
            "queued_at": "2026-05-21T14:30:22+00:00",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "failure": None,
        }
    )

    def cancel_task(_interval: float) -> None:
        store.transition(task_id, TaskStatus.QUEUED, TaskStatus.KILLED)

    monkeypatch.setattr(task_reader_module.time, "sleep", cancel_task)

    events = list(TaskReader(tmp_path).stream_output(task_id))

    assert [event["status"] for event in events] == ["queued", "killed", "killed"]
    assert events[-1]["type"] == "done"


def test_queued_setup_task_is_available_for_resume(tmp_path: Path) -> None:
    from admin.backend.api.v1.setup import running_setup_task

    task_id = "20260521-143022-aabbcc"
    TaskStore(tmp_path).create_queued(
        {
            "task_id": task_id,
            "command": "wizard-setup",
            "args": {},
            "queued_at": "2026-05-21T14:30:22+00:00",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "failure": None,
        }
    )

    assert running_setup_task(tmp_path).task_id == task_id


def test_reader_derives_queue_positions_from_fifo_order(tmp_path: Path) -> None:
    task_ids = [
        "20260521-143022-ffffff",
        "20260521-143022-000000",
        "20260521-143022-aaaaaa",
    ]
    for sequence, task_id in enumerate(task_ids, start=1):
        task_dir = _make_task_dir(tmp_path / "tasks", task_id, status="queued")
        meta = json.loads((task_dir / "meta.json").read_text())
        meta["queue_sequence"] = sequence
        (task_dir / "meta.json").write_text(json.dumps(meta))

    tasks = {task.task_id: task for task in TaskReader(tmp_path).list_tasks()}

    assert [tasks[task_id].queue_position for task_id in task_ids] == [1, 2, 3]
    assert TaskReader(tmp_path).read_task(task_ids[1]).queue_position == 2


def test_reader_ignores_staged_task_dirs(tmp_path: Path) -> None:
    task_id = "20260521-143022-aabbcc"
    _make_task_dir(tmp_path / "tasks", task_id)
    staged_dir = tmp_path / "tasks" / ".20260521-143022-bbccdd.tmp"
    staged_dir.mkdir()
    (staged_dir / "meta.json").write_text(
        json.dumps(
            {
                "task_id": "20260521-143022-bbccdd",
                "command": "build",
                "args": {},
                "started_at": "2026-05-21T14:30:22+00:00",
                "finished_at": None,
                "exit_code": None,
            }
        )
    )
    (staged_dir / "status").write_text("running")

    tasks = TaskReader(tmp_path).list_tasks()

    assert [task.task_id for task in tasks] == [task_id]


def test_reader_ignores_invalid_task_dirs(tmp_path: Path) -> None:
    task_id = "20260521-143022-aabbcc"
    _make_task_dir(tmp_path / "tasks", task_id)
    invalid_dir = tmp_path / "tasks" / "not-a-task"
    invalid_dir.mkdir()
    (invalid_dir / "meta.json").write_text(
        json.dumps(
            {
                "task_id": "20260521-143022-bbccdd",
                "command": "build",
                "args": {},
                "started_at": "2026-05-21T14:30:22+00:00",
                "finished_at": None,
                "exit_code": None,
            }
        )
    )
    (invalid_dir / "status").write_text("running")

    tasks = TaskReader(tmp_path).list_tasks()

    assert [task.task_id for task in tasks] == [task_id]


def test_reader_returns_only_allowlisted_failure_message(tmp_path: Path) -> None:
    task_id = "20260521-143022-aabbcc"
    task_dir = _make_task_dir(tmp_path / "tasks", task_id, status="failed")
    meta = json.loads((task_dir / "meta.json").read_text())
    meta["failure"] = {
        "code": "arbitrary_internal_error",
        "message": "database-password-must-not-leak",
    }
    (task_dir / "meta.json").write_text(json.dumps(meta))

    task = TaskReader(tmp_path).read_task(task_id)

    assert task.failure is not None
    assert task.failure.code == "command_failed"
    assert task.failure.message == "Task command failed."
    assert "database-password" not in str(task.as_dict())


def test_collapse_cr_no_cr() -> None:
    assert collapse_cr("hello world") == "hello world"


def test_collapse_cr_takes_last_segment() -> None:
    assert collapse_cr("[50%]\r[60%]\r[70%]") == "[70%]"


def test_collapse_cr_leading_cr() -> None:
    assert collapse_cr("\rUpdating [93%]") == "Updating [93%]"


def test_collapse_cr_trailing_cr_ignored() -> None:
    assert collapse_cr("[100%]\r") == "[100%]"


def test_collapse_cr_crlf_keeps_text() -> None:
    # dpkg/apt emit CRLF line endings without a TTY; the \r must not blank the
    # line out (this is what produced a wall of empty rows in the setup wizard).
    assert collapse_cr("Unpacking package\r") == "Unpacking package"


def test_collapse_cr_cleared_progress_padding() -> None:
    # apt clears a progress line by overwriting it with spaces after a \r; the
    # padding must collapse away to the last real segment, not leak spaces.
    assert collapse_cr("Fetching\r        ") == "Fetching"


def test_collapse_cr_all_whitespace_segments() -> None:
    assert collapse_cr("   \r   ") == ""


def test_read_output_crlf_lines_not_blank(tmp_path: Path) -> None:
    # Regression: CRLF-terminated apt/dpkg output used to render as empty rows.
    task_id = "20260521-143022-aabbcc"
    task_dir = _make_task_dir(tmp_path / "tasks", task_id)
    (task_dir / "output.log").write_bytes(b"Setting up mariadb\r\nUnpacking redis\r\n")

    reader = TaskReader(tmp_path)
    with patch("os.kill", return_value=None):
        result = reader.read_output(task_id, lines=200)

    assert result == ["Setting up mariadb", "Unpacking redis"]


def test_read_output_collapses_cr_lines(tmp_path: Path) -> None:
    task_id = "20260521-143022-aabbcc"
    task_dir = _make_task_dir(tmp_path / "tasks", task_id)
    # Simulate progress bar: three \r-terminated updates, then \n
    raw = b"[50%]\r[60%]\r[70%]\nDone\n"
    (task_dir / "output.log").write_bytes(raw)

    reader = TaskReader(tmp_path)
    with patch("os.kill", return_value=None):
        result = reader.read_output(task_id, lines=200)

    assert result == ["[70%]", "Done"]


def test_task_retention_limit(tmp_path: Path) -> None:
    runner = TaskRunner(tmp_path)

    # Pre-create TASK_RETENTION_LIMIT + 1 completed tasks directly on disk
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()

    total_pre = TASK_RETENTION_LIMIT + 1
    completed_ids: list[str] = []
    for i in range(total_pre):
        task_id = f"20260521-{i:06d}-aabbcc"
        task_dir = tasks_dir / task_id
        task_dir.mkdir()
        meta = {
            "task_id": task_id,
            "command": "build",
            "args": {},
            "command_argv": ["/usr/bin/bench", "frappe", "build"],
            "started_at": f"2026-05-21T{i // 3600:02d}:{(i % 3600) // 60:02d}:{i % 60:02d}+00:00",
            "finished_at": f"2026-05-21T{i // 3600:02d}:{(i % 3600) // 60:02d}:{i % 60:02d}+00:00",
            "exit_code": 0,
        }
        (task_dir / "meta.json").write_text(json.dumps(meta))
        (task_dir / "status").write_text("success")
        (task_dir / "pid").write_text("99998")
        (task_dir / "output.log").write_text("")
        completed_ids.append(task_id)

    oldest_id = sorted(completed_ids)[0]
    oldest_dir = tasks_dir / oldest_id
    assert oldest_dir.exists()

    with patch("pilot.internal.tasks.runner.task_workers.wake", return_value=True):
        runner.run("build", {})

    # The oldest completed task directory should have been removed.
    assert not oldest_dir.exists()

    # Completed tasks on disk should now equal TASK_RETENTION_LIMIT
    remaining_completed = [
        entry
        for entry in tasks_dir.iterdir()
        if entry.is_dir()
        and (entry / "status").exists()
        and (entry / "status").read_text().strip() in {"success", "failed", "killed"}
    ]
    assert len(remaining_completed) == TASK_RETENTION_LIMIT
