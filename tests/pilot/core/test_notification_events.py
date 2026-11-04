import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pilot.config import BenchConfig
from pilot.core.notification import NotificationStore
from pilot.core.notification.events import record_alert, task_failed, task_succeeded


@pytest.fixture
def bench_root(tmp_path):
    root = tmp_path / "bench"
    root.mkdir()
    root.joinpath("bench.toml").write_text(BenchConfig.from_flat("bench", {}).dumps())
    return root


def _feed(root):
    return NotificationStore(root / "logs").list(10)


def test_task_failure_lands_under_the_matching_category(bench_root) -> None:
    task_failed(bench_root, {"task_id": "t1", "command": "backup-site", "args": {"site": "a.local"}})

    (item,) = _feed(bench_root)
    assert item.title == "Backup site failed"
    assert item.category == "Sites"
    assert item.severity == "Error"
    assert item.site == "a.local"
    assert item.task_id == "t1"
    assert item.action_route == "/insights/tasks/t1"


def test_update_commands_are_grouped_separately(bench_root) -> None:
    task_failed(bench_root, {"task_id": "t2", "command": "migrate", "args": {}})

    (item,) = _feed(bench_root)
    assert item.category == "Updates"
    assert item.site is None


def test_unmapped_command_falls_back_to_tasks(bench_root) -> None:
    task_failed(bench_root, {"task_id": "t3", "command": "install-app", "args": {}})

    assert _feed(bench_root)[0].category == "Tasks"


def test_noisy_commands_stay_out_of_the_feed(bench_root) -> None:
    task_failed(bench_root, {"task_id": "t4", "command": "clear-cache", "args": {}})
    task_failed(bench_root, {"task_id": "t5", "command": "", "args": {}})

    assert _feed(bench_root) == []


def test_task_failure_never_raises(bench_root, caplog) -> None:
    caplog.set_level(logging.WARNING)
    with patch("pilot.core.notification.NotificationStore.create", side_effect=OSError("disk full")):
        task_failed(bench_root, {"task_id": "t6", "command": "update", "args": {}})

    assert "Notification skipped for task_failed: disk full" in caplog.text


def test_a_task_event_without_a_bench_never_raises(tmp_path, caplog) -> None:
    caplog.set_level(logging.WARNING)

    task_failed(tmp_path, {"task_id": "t7", "command": "update", "args": {}})

    assert "Notification skipped for task t7" in caplog.text


def test_task_events_reach_webhooks_with_the_task_context(bench_root) -> None:
    sent = []
    with (
        patch("pilot.core.alerts.notify", lambda bench, payload: sent.append(payload)),
    ):
        task_failed(bench_root, {"task_id": "t8", "command": "backup-site", "args": {"site": "a.local"}})

    (payload,) = sent
    assert payload["event"] == "task_failed"
    assert payload["title"] == "Backup site failed"
    assert payload["severity"] == "Error"
    assert payload["context"]["command"] == "backup-site"
    assert payload["context"]["task_id"] == "t8"
    assert payload["context"]["bench"] == "bench"


def test_a_finished_long_operation_has_its_own_event_and_shows_in_the_feed(bench_root) -> None:
    sent = []
    with patch("pilot.core.alerts.notify", lambda bench, payload: sent.append(payload)):
        task_succeeded(bench_root, {"task_id": "t9", "command": "complete-migration", "args": {}})
        task_succeeded(bench_root, {"task_id": "t10", "command": "clear-cache", "args": {}})

    (item,) = _feed(bench_root)
    assert item.title == "Update finished"
    assert item.severity == "Info"
    assert [payload["event"] for payload in sent] == ["update_completed"]


@pytest.mark.parametrize(
    ("command", "event"),
    [
        ("drop-site", "site_deleted"),
        ("rename-site", "site_renamed"),
        ("backup-site", "backup_completed"),
        ("delete-backup", "backup_deleted"),
        ("install-app", "app_installed"),
        ("get-and-install-app", "app_installed"),
        ("uninstall-app", "app_uninstalled"),
        ("get-app", "app_added"),
        ("remove-app", "app_removed"),
        ("switch-branch", "app_branch_switched"),
        ("setup-letsencrypt", "certificate_issued"),
        ("restart-services", "update_restored"),
        ("dismiss-migration", "update_dismissed"),
    ],
)
def test_routine_operations_go_to_webhooks_but_not_the_feed(bench_root, command: str, event: str) -> None:
    sent = []
    with patch("pilot.core.alerts.notify", lambda bench, payload: sent.append(payload)):
        task_succeeded(bench_root, {"task_id": "t11", "command": command, "args": {"site": "a.local"}})

    assert [(payload["event"], payload["severity"]) for payload in sent] == [(event, "Info")]
    assert _feed(bench_root) == []


def test_every_success_event_is_one_a_webhook_can_choose() -> None:
    from pilot.config.webhook import WEBHOOK_EVENTS
    from pilot.core.notification.events import _SUCCESS_EVENTS

    assert {event for event, _, _ in _SUCCESS_EVENTS.values()} <= WEBHOOK_EVENTS.keys()


def test_the_event_context_carries_task_arguments_but_never_secrets(bench_root) -> None:
    sent = []
    args = {"name": "a.local", "admin_password": "s3cret", "apps": ["erpnext"], "repo": "https://tok@git.example"}
    with patch("pilot.core.alerts.notify", lambda bench, payload: sent.append(payload)):
        task_succeeded(bench_root, {"task_id": "t12", "command": "new-site", "args": args})

    (payload,) = sent
    assert payload["event"] == "site_created"
    assert payload["context"]["name"] == "a.local"
    assert payload["context"]["apps"] == ["erpnext"]
    assert "s3cret" not in str(payload)
    assert "tok@" not in str(payload)


def test_record_alert_writes_the_benchs_own_copy(tmp_path) -> None:
    bench = SimpleNamespace(notifications=NotificationStore(tmp_path / "logs"))
    payload = {"event": "site_down", "message": "bench: a.local unreachable"}

    record_alert(bench, payload, category="Sites", severity="Error", title="a.local is unreachable", site="a.local")

    (item,) = _feed(tmp_path)
    assert item.title == "a.local is unreachable"
    assert item.event == "site_down"
    assert item.message == "bench: a.local unreachable"
    assert item.action_route == "/sites/a.local"


def test_record_alert_never_raises(tmp_path, caplog) -> None:
    bench = SimpleNamespace(notifications=NotificationStore(tmp_path / "logs"))
    caplog.set_level(logging.WARNING)
    with patch("pilot.core.notification.NotificationStore.create", side_effect=OSError("disk full")):
        record_alert(bench, {"event": "resource_limit_breached"}, category="Server", severity="Warning", title="x")

    assert "Notification skipped for alert resource_limit_breached" in caplog.text


def test_a_new_site_task_names_its_site(bench_root) -> None:
    task_failed(bench_root, {"task_id": "t11", "command": "new-site", "args": {"name": "b.local"}})

    (item,) = _feed(bench_root)
    assert item.site == "b.local"
    assert item.message == "b.local: see the task log for the failure."
