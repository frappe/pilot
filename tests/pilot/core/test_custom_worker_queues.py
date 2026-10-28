from __future__ import annotations

import json
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.config import BenchConfig, WorkerGroup
from pilot.config.worker import WorkerConfig, parse_timeout
from pilot.core.bench.settings import regenerate_configs
from pilot.exceptions import ConfigError, MalformedSiteConfig
from tests.pilot.commands.test_commands import make_bench


def _bench(tmp_path: Path, *groups: WorkerGroup, common_site_config: dict | None = None):
    bench = make_bench(tmp_path)
    bench.sites_path.mkdir(parents=True, exist_ok=True)
    bench.config.workers.groups.extend(groups)
    if common_site_config is not None:
        (bench.sites_path / "common_site_config.json").write_text(json.dumps(common_site_config))
    return bench


def _workers(bench) -> dict | None:
    return json.loads((bench.sites_path / "common_site_config.json").read_text()).get("workers")


def test_frappe_learns_the_custom_queues_and_their_timeouts(tmp_path: Path) -> None:
    bench = _bench(
        tmp_path,
        WorkerGroup(queues=["guard-review"], count=1, timeout=1500),
        WorkerGroup(queues=["guard-base"], count=1),
    )

    bench.write_common_site_config()

    assert _workers(bench) == {"guard-review": {"timeout": 1500}, "guard-base": {}}


def test_a_frappe_queue_is_listed_only_with_a_timeout(tmp_path: Path) -> None:
    bench = _bench(tmp_path, common_site_config={"workers": {"short": {"timeout": 60}}})
    next(group for group in bench.config.workers.groups if group.queues == ["long"]).timeout = 3000

    bench.write_common_site_config()

    assert _workers(bench) == {"long": {"timeout": 3000}}


def test_clearing_a_timeout_resets_the_queue(tmp_path: Path) -> None:
    bench = _bench(
        tmp_path,
        WorkerGroup(queues=["guard-review"], count=1),
        common_site_config={"workers": {"guard-review": {"timeout": 1500}, "long": {"timeout": 3000}}},
    )

    bench.write_common_site_config()

    assert _workers(bench) == {"guard-review": {}}


def test_queues_no_group_lists_are_left_alone(tmp_path: Path) -> None:
    bench = _bench(tmp_path, common_site_config={"workers": {"app-queue": {"timeout": 900}}})

    bench.write_common_site_config()

    assert _workers(bench) == {"app-queue": {"timeout": 900}}


def test_no_queue_to_list_leaves_no_workers_key(tmp_path: Path) -> None:
    bench = _bench(tmp_path)

    bench.write_common_site_config()

    assert _workers(bench) is None


@pytest.mark.parametrize("workers", [["guard-review"], "guard-review"])
def test_a_malformed_workers_value_fails_loudly(tmp_path: Path, workers) -> None:
    bench = _bench(
        tmp_path, WorkerGroup(queues=["guard-review"], count=1), common_site_config={"workers": workers}
    )

    with pytest.raises(MalformedSiteConfig):
        bench.write_common_site_config()

    assert _workers(bench) == workers


def test_saved_settings_rewrite_common_site_config(tmp_path: Path) -> None:
    bench = _bench(tmp_path, WorkerGroup(queues=["guard-review"], count=1))

    with (
        patch("pilot.managers.redis.RedisManager"),
        patch("pilot.managers.processes.local.ProcessManager.for_bench", return_value=MagicMock()),
    ):
        regenerate_configs(bench)

    assert _workers(bench) == {"guard-review": {}}


@pytest.mark.parametrize("timeout", [0, -5, "600", True, 600.5])
def test_a_timeout_must_be_positive_seconds(tmp_path: Path, timeout) -> None:
    bench = _bench(tmp_path, WorkerGroup(queues=["guard-review"], count=1, timeout=timeout))

    with pytest.raises(ConfigError, match="timeout must be a positive number of seconds"):
        bench.config.workers.validate()


def test_a_queue_cannot_have_two_timeouts(tmp_path: Path) -> None:
    bench = _bench(
        tmp_path,
        WorkerGroup(queues=["guard-review"], count=1, timeout=600),
        WorkerGroup(queues=["guard-review", "guard-base"], count=1, timeout=900),
    )

    with pytest.raises(ConfigError, match=r"Queue 'guard-review' has two timeouts: 600 and 900 seconds\."):
        bench.config.workers.validate()


def test_a_timeout_round_trips_through_bench_toml(tmp_path: Path) -> None:
    config = _bench(tmp_path, WorkerGroup(queues=["guard-review"], count=2, timeout=1500)).config
    path = tmp_path / "bench.toml"
    path.write_text(config.dumps())

    groups = BenchConfig.from_file(path).workers.groups

    assert [group.timeout for group in groups] == [None, None, None, 1500]
    assert [entry.get("timeout") for entry in tomllib.loads(path.read_text())["workers"]] == [
        None,
        None,
        None,
        1500,
    ]


def test_lite_mode_keeps_the_longest_timeout(tmp_path: Path) -> None:
    workers = _bench(
        tmp_path,
        WorkerGroup(queues=["guard-review"], count=1, timeout=600),
        WorkerGroup(queues=["guard-base"], count=1, timeout=1500),
    ).config.workers

    workers.collapse()

    assert workers.groups == [
        WorkerGroup(queues=["default", "short", "long", "guard-review", "guard-base"], count=6, timeout=1500)
    ]


def test_lite_mode_counts_a_blank_timeout_as_frappes_default() -> None:
    workers = WorkerConfig(
        groups=[
            WorkerGroup(queues=["default", "short", "long"], count=1),
            WorkerGroup(queues=["guard-review"], count=1, timeout=120),
        ]
    )

    workers.collapse()

    assert workers.groups[0].timeout == 1500


def test_lite_mode_keeps_frappes_defaults_when_no_group_sets_a_timeout() -> None:
    workers = WorkerConfig(
        groups=[
            WorkerGroup(queues=["default", "short", "long"], count=1),
            WorkerGroup(queues=["guard-review"], count=1),
        ]
    )

    workers.collapse()

    assert workers.groups[0].timeout is None


@pytest.mark.parametrize(
    ("value", "parsed"),
    [(None, None), ("", None), ("  ", None), (" 600 ", 600), (600, 600), ("soon", "soon"), (True, True)],
)
def test_parse_timeout(value, parsed) -> None:
    assert parse_timeout(value) == parsed
