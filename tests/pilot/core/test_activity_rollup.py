from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pilot.core.site.activity_rollup import SiteActivityRollup
from pilot.core.site.uptime_monitoring import PING_PATH
from pilot.internal.atomic_file import exclusive_file_lock

NOW = datetime(2026, 11, 12, 15, 30, tzinfo=UTC)
SITE = "one.example.com"


def _rollup(tmp_path: Path) -> SiteActivityRollup:
    (tmp_path / "logs").mkdir(exist_ok=True)
    return SiteActivityRollup(SimpleNamespace(logs_path=tmp_path / "logs"))


def _request(
    path: str, when: str = "2026-11-12 15:10:00+00:00", status: int = 200, duration: int = 1000, site=SITE
):
    return {
        "timestamp": when,
        "site": site,
        "transaction_type": "request",
        "duration": duration,
        "request": {"path": path, "status_code": status},
    }


def _job(method: str, duration: int = 2_000_000) -> dict:
    return {
        "timestamp": "2026-11-12 15:10:00+00:00",
        "site": SITE,
        "transaction_type": "job",
        "duration": duration,
        "job": {"method": method},
    }


def _write(rollup: SiteActivityRollup, *records: dict) -> None:
    with rollup.source.open("a") as log:
        log.writelines(json.dumps(record, separators=(",", ":")) + "\n" for record in records)


def test_activity_counts_requests_errors_and_jobs_by_hour(tmp_path: Path) -> None:
    rollup = _rollup(tmp_path)
    _write(
        rollup,
        _request("/app/todo", duration=100_000),
        _request("/app/todo", duration=300_000),
        _request("/api/x", status=502, duration=5_000_000),
        _request(PING_PATH, duration=9_000_000),
        _job("erp.reorder"),
        _request("/app/old", when="2026-11-12 14:59:00+00:00"),
    )

    rollup.update(NOW)
    activity = rollup.get_activity(SITE, NOW)

    assert len(activity.hours) == 24
    assert activity.requests[-2:] == [1, 3]
    assert activity.errors[-1] == 1
    assert activity.jobs[-1] == 1
    assert activity.response_ms[-1] == round((100_000 + 300_000 + 5_000_000) / 3 / 1000, 1)
    assert (activity.top_paths[0].name, activity.top_paths[0].value) == ("/app/todo", 2)
    assert {item.name for item in activity.top_paths} == {"/app/todo", "/api/x", "/app/old"}
    assert [item.name for item in activity.failed_paths] == ["/api/x"]
    assert activity.slow_requests[0].name == PING_PATH
    assert activity.slow_requests[0].value == 9.0
    assert [(item.name, item.value) for item in activity.slow_jobs] == [("erp.reorder", 2.0)]


def test_updates_add_only_new_lines_into_each_site_file(tmp_path: Path) -> None:
    rollup = _rollup(tmp_path)
    _write(rollup, _request("/a"), _request("/b", site="two.example.com"))
    rollup.update(NOW)
    _write(rollup, _request("/a"))
    rollup.update(NOW)
    rollup.update(NOW)

    assert rollup.get_activity(SITE, NOW).requests[-1] == 2
    assert rollup.get_activity("two.example.com", NOW).requests[-1] == 1
    assert sorted(path.name for path in rollup.directory.glob("*.json")) == [
        "offset.json",
        "one.example.com.json",
        "two.example.com.json",
    ]


def test_path_lists_keep_only_the_top_names_per_hour(tmp_path: Path) -> None:
    rollup = _rollup(tmp_path)
    rollup.top_limit = 3
    _write(rollup, *[_request(f"/p{index}") for index in range(10)], *[_request("/hot")] * 5)

    rollup.update(NOW)

    names = [item.name for item in rollup.get_activity(SITE, NOW).top_paths]
    assert names[0] == "/hot"
    assert len(names) == 3


def test_a_capped_update_reads_one_step(tmp_path: Path) -> None:
    rollup = _rollup(tmp_path)
    _write(rollup, *[_request("/a")] * 100)
    line_size = len(rollup.source.read_bytes()) // 100

    rollup.step_bytes = line_size * 10

    rollup.update(NOW, catch_up=False)
    assert rollup.get_activity(SITE, NOW).requests[-1] == 10

    rollup.update(NOW, catch_up=False)
    assert rollup.get_activity(SITE, NOW).requests[-1] == 20


def test_an_update_skips_when_another_holds_the_lock(tmp_path: Path) -> None:
    rollup = _rollup(tmp_path)
    _write(rollup, _request("/a"))
    rollup.directory.mkdir(parents=True)

    with exclusive_file_lock(rollup.directory / "update"):
        assert rollup.update(NOW) is False

    assert rollup.update(NOW) is True
    assert rollup.get_activity(SITE, NOW).requests[-1] == 1


def test_site_names_that_are_not_file_names_are_ignored(tmp_path: Path) -> None:
    rollup = _rollup(tmp_path)
    _write(
        rollup, _request("/a", site="../escape"), _request("/a", site=".hidden"), _request("/a", site="a/b")
    )

    rollup.update(NOW)

    assert not (tmp_path / "logs" / "escape.json").exists()
    assert sorted(path.name for path in rollup.directory.iterdir() if path.suffix == ".json") == [
        "offset.json"
    ]
    assert rollup.get_activity("../escape", NOW).requests[-1] == 0


def test_hours_older_than_a_day_are_dropped(tmp_path: Path) -> None:
    rollup = _rollup(tmp_path)
    _write(rollup, _request("/a"))
    rollup.update(NOW)
    later = datetime(2026, 11, 14, 15, 30, tzinfo=UTC)
    _write(rollup, _request("/b", when="2026-11-14 15:00:00+00:00"))

    rollup.update(later)

    saved = json.loads((rollup.directory / f"{SITE}.json").read_text())
    assert list(saved) == ["2026-11-14T15"]


def test_a_first_update_skips_lines_older_than_the_window(tmp_path: Path) -> None:
    rollup = _rollup(tmp_path)
    old = [
        _request("/old", when=f"2026-11-{day:02d} 10:00:00+00:00") for day in range(1, 11) for _ in range(400)
    ]
    _write(rollup, *old, _request("/new"))

    with patch.object(SiteActivityRollup, "add", autospec=True, side_effect=SiteActivityRollup.add) as add:
        rollup.update(NOW)

    assert add.call_count < 100
    assert [item.name for item in rollup.get_activity(SITE, NOW).top_paths] == ["/new"]


def test_a_full_update_catches_up_one_step_at_a_time(tmp_path: Path) -> None:
    rollup = _rollup(tmp_path)
    _write(rollup, *[_request("/a")] * 100)
    rollup.step_bytes = len(rollup.source.read_bytes()) // 7

    with patch.object(SiteActivityRollup, "fold", autospec=True, side_effect=SiteActivityRollup.fold) as fold:
        rollup.update(NOW)

    assert fold.call_count >= 7
    assert rollup.get_activity(SITE, NOW).requests[-1] == 100


def test_a_full_update_removes_files_of_idle_sites(tmp_path: Path) -> None:
    rollup = _rollup(tmp_path)
    _write(rollup, _request("/a"))
    rollup.update(NOW)
    dropped = rollup.directory / "dropped.example.com.json"
    dropped.write_text("{}")
    old = NOW.timestamp() - 2 * 86400
    os.utime(dropped, (old, old))

    rollup.update(NOW, catch_up=False)
    assert dropped.exists()

    rollup.update(NOW)
    assert not dropped.exists()
    assert (rollup.directory / f"{SITE}.json").exists()
