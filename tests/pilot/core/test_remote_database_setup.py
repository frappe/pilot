from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pilot.config.common import CommonConfig
from pilot.core.bench import Bench
from pilot.exceptions import BenchError
from pilot.managers.database.mariadb import MariaDBManager
from pilot.managers.database.remote_mariadb import RemoteMariaDBManager
from pilot.managers.processes.local import ProcessManager
from tests.pilot.commands.test_commands import make_bench


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    path = tmp_path / "benches" / "test-bench"
    (path / "sites").mkdir(parents=True)
    bench = make_bench(path)
    bench.config.write(bench.path)
    return bench


@pytest.fixture
def steps():
    """Record the remote takeover, the unit rebuild and the local removal instead of running them."""
    calls = MagicMock()
    with (
        patch.object(RemoteMariaDBManager, "provision", lambda self: calls.provision(self.config)),
        patch.object(
            MariaDBManager,
            "remove_local_server",
            lambda self, keep_data=False: calls.remove_local_server(self.config, keep_data=keep_data),
        ),
        patch.object(
            Bench, "rebuild_process_set", lambda self, on_progress: calls.rebuild_process_set(self.path)
        ),
    ):
        yield calls


def test_the_host_moves_to_the_remote_server_and_drops_the_local_one(bench: Bench, steps) -> None:
    bench.setup_remote_database("fdaa:1:2::3", "token")

    remote = CommonConfig.read(bench.path.parent).mariadb
    assert (remote.host, remote.port, remote.admin_user) == ("fdaa:1:2::3", 3306, "pilot")
    assert (remote.remote, remote.agent_port, remote.agent_token) == (True, 7781, "token")
    assert len(remote.root_password) >= 24
    assert [call[0] for call in steps.mock_calls] == [
        "provision",
        "rebuild_process_set",
        "remove_local_server",
    ]
    assert steps.provision.call_args.args[0] == remote
    assert steps.remove_local_server.call_args.args[0].remote is False
    assert steps.remove_local_server.call_args.kwargs == {"keep_data": False}
    common_site_config = json.loads((bench.sites_path / "common_site_config.json").read_text())
    assert (common_site_config["db_host"], common_site_config["db_port"]) == ("fdaa:1:2::3", 3306)


def test_every_bench_on_the_host_moves_together(bench: Bench, steps) -> None:
    other = make_bench(bench.path.parent / "other-bench")
    other.config.name = "other-bench"
    (other.path / "sites").mkdir(parents=True)
    other.config.write(other.path)

    bench.setup_remote_database("fdaa:1:2::3", "token")

    assert {call.args[0] for call in steps.rebuild_process_set.call_args_list} == {bench.path, other.path}
    assert "fdaa:1:2::3" in (other.sites_path / "common_site_config.json").read_text()


def test_a_host_with_sites_is_refused_before_any_change(bench: Bench, steps) -> None:
    site = bench.sites_path / "site.local"
    site.mkdir()
    (site / "site_config.json").write_text("{}")

    with pytest.raises(BenchError, match="site.local.*--migrate"):
        bench.setup_remote_database("fdaa:1:2::3", "token")

    assert steps.mock_calls == []
    assert CommonConfig.read(bench.path.parent).mariadb.remote is False


@pytest.mark.parametrize(
    ("host", "token", "message"),
    [("fdaa:1:2::3", "", "agent_token"), ("localhost", "token", "remote server")],
)
def test_an_unusable_server_is_refused_before_any_change(
    bench: Bench, steps, host: str, token: str, message: str
) -> None:
    with pytest.raises(Exception, match=message):
        bench.setup_remote_database(host, token)

    assert steps.mock_calls == []


def test_an_existing_server_is_left_alone(bench: Bench, steps) -> None:
    bench.config.mariadb.existing = True

    with pytest.raises(BenchError, match="existing"):
        bench.setup_remote_database("fdaa:1:2::3", "token")

    assert steps.mock_calls == []


def test_a_host_already_on_a_remote_server_is_refused(bench: Bench, steps) -> None:
    bench.config.mariadb.host, bench.config.mariadb.remote = "fdaa::9", True

    with pytest.raises(BenchError, match="already uses the MariaDB server on fdaa::9"):
        bench.setup_remote_database("fdaa:1:2::3", "token")

    assert steps.mock_calls == []


class FakeServer:
    """A MariaDB server that answers the SQL the move runs. `databases` maps a name to its objects."""

    def __init__(self, calls: MagicMock, databases: dict[str, set[str]], accounts: set[str]) -> None:
        self.calls = calls
        self.databases = databases
        self.accounts = accounts
        self.net_write_timeout = 60

    def run_admin_sql(self, sql: str) -> str:
        if match := re.match(r"SET GLOBAL net_write_timeout = (\d+)", sql):
            self.net_write_timeout = int(match[1])
            return ""
        if match := re.match(r"CREATE DATABASE `([^`]+)`", sql):
            self.databases[match[1]] = set()
            self.accounts.add(re.findall(r"CREATE USER ('[^']+'@'[^']+')", sql)[0])
            self.calls.create(match[1])
        elif match := re.match(r"DROP DATABASE IF EXISTS `([^`]+)`", sql):
            self.databases.pop(match[1], None)
            self.accounts.discard(re.findall(r"DROP USER IF EXISTS ('[^']+'@'[^']+')", sql)[0])
            self.calls.drop(match[1])
        return "".join(f"{row}\n" for row in ["column", *rows]) if (rows := self.get_rows(sql)) else ""

    def get_rows(self, sql: str) -> list[str]:
        if "net_write_timeout" in sql:
            return [str(self.net_write_timeout)]
        if "VERSION()" in sql:
            return ["11.8.6-MariaDB"]
        if "DATA_LENGTH" in sql:
            return [str(40 * 1024**3)]
        if "SCHEMATA" in sql:
            names = [f"database {name}" for name in self.databases if f"'{name}'" in sql]
            return names + [f"account {account}" for account in self.accounts if account.split("@")[0] in sql]
        if "SECURITY_TYPE" not in sql and (match := re.search(r"TABLE_SCHEMA = '([^']+)'", sql)):
            return sorted(self.databases.get(match[1], ()))
        return []


@pytest.fixture
def migration(bench: Bench, steps):
    """A host with one MariaDB site, one SQLite site and a running production bench, against fake servers."""
    bench.config.production.enabled, bench.config.production.process_manager = True, "systemd"
    for name, config in {
        "site.local": {
            "db_name": "_site",
            "db_password": "site-secret",
            "db_host": "127.0.0.1",
            "db_port": 3310,
        },
        "lite.local": {"db_type": "sqlite", "db_name": "lite"},
    }.items():
        (bench.sites_path / name).mkdir()
        (bench.sites_path / name / "site_config.json").write_text(json.dumps(config))

    local = FakeServer(steps, {"_site": {"BASE TABLE tabUser", "VIEW tabReport"}}, {"'_site'@'localhost'"})
    remote = FakeServer(steps, {"other": {"BASE TABLE kept"}}, set())
    workload = MagicMock(bench=bench, is_running=lambda: workload.running)
    workload.running = True
    workload.stop.side_effect = lambda: (setattr(workload, "running", False), steps.stop())
    workload.start_workload.side_effect = lambda: (setattr(workload, "running", True), steps.start())
    agent = MagicMock(**{"get_free_disk_bytes.return_value": 100 * 1024**3})

    def stream(dump, restore, source_env, target_env):
        steps.stream(dump, restore, source_env["MYSQL_PWD"], target_env["MYSQL_PWD"])
        remote.databases[restore[-1]] |= local.databases[dump[-1]]

    with (
        patch.object(
            MariaDBManager,
            "run_admin_sql",
            lambda self, sql: (remote if self.config.remote else local).run_admin_sql(sql),
        ),
        patch.object(MariaDBManager, "has_valid_credentials", lambda self: True),
        patch.object(RemoteMariaDBManager, "get_health", lambda self: {"version": [11, 8]}),
        patch.object(RemoteMariaDBManager, "get_account_host", lambda self: "fdaa::7"),
        patch.object(RemoteMariaDBManager, "agent", agent),
        patch.object(ProcessManager, "for_bench", staticmethod(lambda bench: workload)),
        patch(
            "pilot.core.bench.remote_database.site_database.stream_database", side_effect=stream
        ) as streamer,
    ):
        yield SimpleNamespace(local=local, remote=remote, agent=agent, streamer=streamer)


@pytest.mark.parametrize("delete_local_data", [False, True])
def test_migrate_copies_each_site_database_then_switches_the_host(
    bench: Bench, steps, migration, delete_local_data: bool
) -> None:
    messages: list[str] = []

    bench.setup_remote_database(
        "fdaa:1:2::3", "token", migrate=True, delete_local_data=delete_local_data, on_progress=messages.append
    )

    assert [call[0] for call in steps.mock_calls] == [
        "provision",
        "stop",
        "create",
        "stream",
        "rebuild_process_set",
        "start",
        "remove_local_server",
    ]
    assert migration.remote.databases["_site"] == {"BASE TABLE tabUser", "VIEW tabReport"}
    assert migration.remote.accounts == {"'_site'@'fdaa::7'"}
    dump, restore, dump_password, restore_password = steps.stream.call_args.args
    assert {"--single-transaction", "--quick", "--routines", "--triggers", "--events"} <= set(dump)
    assert dump[0] == "mariadb-dump" and restore[0] == "mariadb" and restore[-1] == "_site"
    assert (
        dump_password == "root"
        and restore_password == CommonConfig.read(bench.path.parent).mariadb.root_password
    )
    arguments = " ".join([*dump, *restore])
    assert (
        "--password" not in arguments and restore_password not in arguments and "site-secret" not in arguments
    )
    assert migration.local.net_write_timeout == 60
    assert CommonConfig.read(bench.path.parent).mariadb.remote is True
    site_config = json.loads((bench.sites_path / "site.local" / "site_config.json").read_text())
    assert (site_config["db_host"], site_config["db_port"]) == ("fdaa:1:2::3", 3306)
    assert steps.remove_local_server.call_args.kwargs == {"keep_data": not delete_local_data}
    assert any("lite.local" in message for message in messages)
    assert any("rm -rf" in message for message in messages) is not delete_local_data


@pytest.mark.parametrize(
    ("server", "change", "message"),
    [
        ("health", {"version": [11, 4]}, "older than the local server's 11.8"),
        ("names", {"_site": set()}, "already has database _site"),
        ("disk", 10 * 1024**3, "Not enough disk space"),
    ],
)
def test_migrate_refuses_a_remote_server_that_cannot_take_the_copy(
    bench: Bench, steps, migration, server: str, change, message: str
) -> None:
    if server == "health":
        patcher = patch.object(RemoteMariaDBManager, "get_health", lambda self: change)
    elif server == "disk":
        patcher = patch.object(migration.agent, "get_free_disk_bytes", return_value=change)
    else:
        migration.remote.databases.update(change)
        patcher = contextlib.nullcontext()

    with patcher, pytest.raises(BenchError, match=message):
        bench.setup_remote_database("fdaa:1:2::3", "token", migrate=True)

    assert not {"stop", "create", "remove_local_server"} & {call[0] for call in steps.mock_calls}
    assert CommonConfig.read(bench.path.parent).mariadb.remote is False


@pytest.mark.parametrize(
    ("failure", "message"),
    [("copy", "Copying the database of test-bench/site.local failed"), ("verify", "does not match")],
)
def test_a_failed_copy_drops_only_its_copies_and_restarts_on_the_local_server(
    bench: Bench, steps, migration, failure: str, message: str
) -> None:
    common_site_config = bench.sites_path / "common_site_config.json"
    bench.write_common_site_config()
    before = {
        path: path.read_text() for path in [common_site_config, *bench.sites_path.glob("*/site_config.json")]
    }
    copy = migration.streamer.side_effect
    if failure == "copy":
        migration.streamer.side_effect = BenchError("dump exit 2, import exit 1")
    else:
        migration.streamer.side_effect = lambda *args: migration.remote.databases["_site"].add(
            "BASE TABLE stray"
        )

    with pytest.raises(BenchError, match=message):
        bench.setup_remote_database("fdaa:1:2::3", "token", migrate=True)

    assert [call[0] for call in steps.mock_calls if call[0] in ("stop", "create", "drop", "start")] == [
        "stop",
        "create",
        "drop",
        "start",
    ]
    assert migration.remote.databases == {"other": {"BASE TABLE kept"}} and migration.remote.accounts == set()
    assert {path: path.read_text() for path in before} == before
    assert CommonConfig.read(bench.path.parent).mariadb.remote is False
    assert "remove_local_server" not in {call[0] for call in steps.mock_calls}

    migration.streamer.side_effect = copy
    bench.setup_remote_database("fdaa:1:2::3", "token", migrate=True)

    assert CommonConfig.read(bench.path.parent).mariadb.remote is True
