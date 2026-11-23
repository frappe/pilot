from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.config.common import CommonConfig
from pilot.core.bench import Bench
from pilot.exceptions import BenchError
from pilot.managers.database.mariadb import MariaDBManager
from pilot.managers.database.remote_mariadb import RemoteMariaDBManager
from tests.pilot.commands.test_commands import make_bench


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    path = tmp_path / "benches" / "test-bench"
    (path / "sites").mkdir(parents=True)
    bench = make_bench(path)
    bench.config.write(bench.path)
    return bench


@pytest.fixture
def key(tmp_path: Path) -> Path:
    key = tmp_path / "database"
    key.write_text("private key")
    return key


@pytest.fixture
def steps():
    """Record the remote takeover, the unit rebuild and the local removal instead of running them."""
    calls = MagicMock()
    with (
        patch.object(RemoteMariaDBManager, "provision", lambda self: calls.provision(self.config)),
        patch.object(
            MariaDBManager, "remove_local_server", lambda self: calls.remove_local_server(self.config)
        ),
        patch.object(
            Bench, "rebuild_process_set", lambda self, on_progress: calls.rebuild_process_set(self.path)
        ),
    ):
        yield calls


def test_the_host_moves_to_the_remote_server_and_drops_the_local_one(bench: Bench, key: Path, steps) -> None:
    bench.setup_remote_database("fdaa:1:2::3", "ubuntu", str(key))

    remote = CommonConfig.read(bench.path.parent).mariadb
    assert (remote.host, remote.port, remote.admin_user) == ("fdaa:1:2::3", 3306, "pilot")
    assert (remote.remote, remote.ssh_user, remote.ssh_key_path) == (True, "ubuntu", str(key))
    assert len(remote.root_password) >= 24
    assert remote.memory_share == 0.8
    assert [call[0] for call in steps.mock_calls] == [
        "provision",
        "rebuild_process_set",
        "remove_local_server",
    ]
    assert steps.provision.call_args.args[0] == remote
    assert steps.remove_local_server.call_args.args[0].remote is False
    common_site_config = json.loads((bench.sites_path / "common_site_config.json").read_text())
    assert (common_site_config["db_host"], common_site_config["db_port"]) == ("fdaa:1:2::3", 3306)


def test_every_bench_on_the_host_moves_together(bench: Bench, key: Path, steps) -> None:
    other = make_bench(bench.path.parent / "other-bench")
    other.config.name = "other-bench"
    (other.path / "sites").mkdir(parents=True)
    other.config.write(other.path)

    bench.setup_remote_database("fdaa:1:2::3", "ubuntu", str(key))

    assert {call.args[0] for call in steps.rebuild_process_set.call_args_list} == {bench.path, other.path}
    assert "fdaa:1:2::3" in (other.sites_path / "common_site_config.json").read_text()


def test_a_host_with_sites_is_refused_before_any_change(bench: Bench, key: Path, steps) -> None:
    site = bench.sites_path / "site.local"
    site.mkdir()
    (site / "site_config.json").write_text("{}")

    with pytest.raises(BenchError, match="site.local"):
        bench.setup_remote_database("fdaa:1:2::3", "ubuntu", str(key))

    assert steps.mock_calls == []
    assert CommonConfig.read(bench.path.parent).mariadb.remote is False


@pytest.mark.parametrize(
    ("host", "key_name", "message"),
    [("fdaa:1:2::3", "missing", "does not exist"), ("localhost", "database", "remote server")],
)
def test_an_unusable_server_is_refused_before_any_change(
    bench: Bench, key: Path, steps, host: str, key_name: str, message: str
) -> None:
    with pytest.raises(Exception, match=message):
        bench.setup_remote_database(host, "ubuntu", str(key.with_name(key_name)))

    assert steps.mock_calls == []


def test_an_existing_server_is_left_alone(bench: Bench, key: Path, steps) -> None:
    bench.config.mariadb.existing = True

    with pytest.raises(BenchError, match="existing"):
        bench.setup_remote_database("fdaa:1:2::3", "ubuntu", str(key))

    assert steps.mock_calls == []


def test_a_host_already_on_a_remote_server_is_refused(bench: Bench, key: Path, steps) -> None:
    bench.config.mariadb.host, bench.config.mariadb.remote = "fdaa::9", True

    with pytest.raises(BenchError, match="already uses the MariaDB server on fdaa::9"):
        bench.setup_remote_database("fdaa:1:2::3", "ubuntu", str(key))

    assert steps.mock_calls == []
