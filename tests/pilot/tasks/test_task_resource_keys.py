from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.tasks.build import BuildTask
from pilot.tasks.change_admin_domain import ChangeAdminDomainTask
from pilot.tasks.clear_cache import ClearCacheTask
from pilot.tasks.new_site import NewSiteTask
from pilot.tasks.rename_site import RenameSiteTask
from pilot.tasks.restart_database import RestartDatabaseTask
from pilot.tasks.restore_site import RestoreSiteTask
from pilot.tasks.set_mariadb_memory_share import SetMariaDBMemoryShareTask
from pilot.tasks.setup_letsencrypt import SetupLetsEncryptTask
from tests.pilot.commands.test_commands import make_bench


def resource_keys(task_class, tmp_path: Path, **args) -> set[str]:
    return set(task_class.get_queued_task(make_bench(tmp_path), args).resource_keys)


def test_site_keys_are_lowercase_and_hold_the_hostname(tmp_path: Path) -> None:
    keys = resource_keys(NewSiteTask, tmp_path, name="Shop.Example.COM", admin_password="secret")

    assert keys == {"site:shop.example.com", "host:shop.example.com"}


def test_rename_holds_both_names_and_hostnames(tmp_path: Path) -> None:
    keys = resource_keys(RenameSiteTask, tmp_path, site="old.localhost", new_name="new.localhost")

    assert keys == {"site:old.localhost", "site:new.localhost", "host:old.localhost", "host:new.localhost"}


def test_admin_domain_change_holds_the_previous_hostname(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.admin.domain = "old-admin.example.com"

    task = ChangeAdminDomainTask.get_queued_task(bench, {"domain": "new-admin.example.com"})

    assert set(task.resource_keys) == {
        "admin-domain",
        "host:old-admin.example.com",
        "host:new-admin.example.com",
    }


def test_a_rename_and_an_admin_move_to_one_hostname_share_a_resource(tmp_path: Path) -> None:
    rename = resource_keys(RenameSiteTask, tmp_path, site="old.localhost", new_name="shared.example.com")
    move = resource_keys(ChangeAdminDomainTask, tmp_path, domain="shared.example.com")

    assert rename & move == {"host:shared.example.com"}


@pytest.mark.parametrize(
    ("task_class", "args", "expected"),
    [
        (BuildTask, {"site": "a.localhost"}, {"site:a.localhost", "bench:update"}),
        (ClearCacheTask, {"site": "A.localhost"}, {"site:a.localhost"}),
        (SetupLetsEncryptTask, {}, set()),
        (RestoreSiteTask, {"site": "a.localhost", "parts": ["database"]}, {"site:a.localhost"}),
        (
            RestoreSiteTask,
            {"site": "a.localhost", "parts": ["database"], "source_site": "b.localhost"},
            {"site:a.localhost", "site:b.localhost"},
        ),
        (RestartDatabaseTask, {}, {"database-server"}),
        (SetMariaDBMemoryShareTask, {"memory_share": 0.5}, {"database-server"}),
    ],
)
def test_task_resource_keys(tmp_path: Path, task_class, args: dict, expected: set[str]) -> None:
    assert resource_keys(task_class, tmp_path, **args) == expected


def test_queue_holds_the_task_keys_unless_the_caller_replaces_them(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)

    with patch("pilot.internal.tasks.runner.task_workers.wake", return_value=True):
        own = ClearCacheTask.queue(bench, site="a.localhost")
        replaced = ClearCacheTask.queue(bench, site="b.localhost", resource_key="custom")

    def stored_keys(task_id: str) -> list[str]:
        return json.loads((tmp_path / "tasks" / task_id / "meta.json").read_text())["resource_keys"]

    assert stored_keys(own) == ["site:a.localhost"]
    assert stored_keys(replaced) == ["custom"]
