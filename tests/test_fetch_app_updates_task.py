"""Tests for admin.backend.tasks.jobs.fetch_app_updates_task.

Covers the marketplace-aware update check: a tag/commit-pinned app resolves
purely locally by comparing against the marketplace's advertised revision —
no network needed, and a moved pin is always treated as a forward update
since marketplace entries only ever advance. Everything else falls back to
a network-based branch-tip check via App.has_remote_update.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from admin.backend.tasks.jobs.fetch_app_updates_task import FetchAppUpdatesTask
from pilot.core.app import RevisionPin
from pilot.core.marketplace import Marketplace


def app_mock(
    repo: str,
    version: str = "",
    installed_hash: str = "",
    installed_tag: str = "",
    has_remote_update: bool = False,
) -> MagicMock:
    app = MagicMock()
    app.config.repo = repo
    app.installed_version = version
    app.installed_hash = installed_hash
    app.installed_tag = installed_tag
    app.has_remote_update.return_value = has_remote_update

    def is_on_revision(pin: RevisionPin) -> bool:
        if pin.kind == "tag":
            return installed_tag == pin.ref
        return bool(installed_hash) and installed_hash.startswith(pin.ref)

    app.is_on_revision.side_effect = is_on_revision
    return app


def make_task(registry: list[dict], bench: MagicMock, bench_root: Path) -> FetchAppUpdatesTask:
    with patch.object(Marketplace, "registry", return_value=registry):
        return FetchAppUpdatesTask(bench, bench_root, MagicMock())


TAG_REGISTRY = [
    {"name": "helpdesk", "repo": "https://github.com/frappe/helpdesk", "targets": [
        {"version": "1.0.0", "target_type": "tag", "target": "v1.0.0"},
    ]},
]
COMMIT_REGISTRY = [
    {"name": "payments", "repo": "https://github.com/frappe/payments", "targets": [
        {"version": "2.0.0", "target_type": "commit", "target": "abc123"},
    ]},
]
BRANCH_REGISTRY = [
    {"name": "hrms", "repo": "https://github.com/frappe/hrms", "targets": [
        {"version": "3.0.0", "target_type": "branch", "target": "main"},
    ]},
]


# ── _matching_target ─────────────────────────────────────────────────────────


def test_matching_target_found_by_version(tmp_path: Path) -> None:
    bench = MagicMock()
    task = make_task(TAG_REGISTRY, bench, tmp_path)
    app = app_mock("https://github.com/frappe/helpdesk", "1.0.0")
    target = task._matching_target("helpdesk", app)
    assert target == {"version": "1.0.0", "target_type": "tag", "target": "v1.0.0"}


def test_matching_target_none_when_app_not_in_marketplace(tmp_path: Path) -> None:
    bench = MagicMock()
    task = make_task(TAG_REGISTRY, bench, tmp_path)
    app = app_mock("https://github.com/frappe/frappe", "1.0.0")
    assert task._matching_target("frappe", app) is None


def test_matching_target_none_on_repo_mismatch(tmp_path: Path) -> None:
    # A fork with a different repo URL isn't the marketplace's app.
    bench = MagicMock()
    task = make_task(TAG_REGISTRY, bench, tmp_path)
    app = app_mock("https://github.com/someone/helpdesk", "1.0.0")
    assert task._matching_target("helpdesk", app) is None


def test_matching_target_none_on_version_mismatch(tmp_path: Path) -> None:
    bench = MagicMock()
    task = make_task(TAG_REGISTRY, bench, tmp_path)
    app = app_mock("https://github.com/frappe/helpdesk", "9.9.9")
    assert task._matching_target("helpdesk", app) is None


# ── run ──────────────────────────────────────────────────────────────────────


def _make_git_apps(bench_root: Path, names: list[str]) -> None:
    for name in names:
        (bench_root / "apps" / name / ".git").mkdir(parents=True)


def test_run_pinned_tag_app_reports_no_update_without_network(tmp_path: Path, capsys) -> None:
    _make_git_apps(tmp_path, ["helpdesk"])
    app = app_mock("https://github.com/frappe/helpdesk", "1.0.0", installed_tag="v1.0.0")
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=TAG_REGISTRY):
        task = FetchAppUpdatesTask(bench, tmp_path, MagicMock())
        task.run()

    app.has_remote_update.assert_not_called()  # resolved purely from local state
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {"helpdesk": False}


def test_run_reports_update_when_marketplace_tag_moved(tmp_path: Path, capsys) -> None:
    # App is still on v1.0.0 (the version it was installed at) but the
    # marketplace's target for that version has since moved to a new tag —
    # entries only ever advance, so this is always offered as an update,
    # without any network call.
    _make_git_apps(tmp_path, ["helpdesk"])
    app = app_mock("https://github.com/frappe/helpdesk", "1.0.0", installed_tag="v0.9.0")
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=TAG_REGISTRY):
        task = FetchAppUpdatesTask(bench, tmp_path, MagicMock())
        task.run()

    app.has_remote_update.assert_not_called()
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {"helpdesk": True}


def test_run_pinned_commit_app_reports_no_update_without_network(tmp_path: Path, capsys) -> None:
    _make_git_apps(tmp_path, ["payments"])
    app = app_mock("https://github.com/frappe/payments", "2.0.0", installed_hash="abc123def456")
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=COMMIT_REGISTRY):
        task = FetchAppUpdatesTask(bench, tmp_path, MagicMock())
        task.run()

    app.has_remote_update.assert_not_called()
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {"payments": False}


def test_run_reports_update_when_marketplace_commit_moved(tmp_path: Path, capsys) -> None:
    _make_git_apps(tmp_path, ["payments"])
    app = app_mock("https://github.com/frappe/payments", "2.0.0", installed_hash="deadbeef00")
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=COMMIT_REGISTRY):
        task = FetchAppUpdatesTask(bench, tmp_path, MagicMock())
        task.run()

    app.has_remote_update.assert_not_called()
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {"payments": True}


def test_run_branch_target_falls_back_to_remote_check(tmp_path: Path, capsys) -> None:
    _make_git_apps(tmp_path, ["hrms"])
    app = app_mock("https://github.com/frappe/hrms", "3.0.0", has_remote_update=True)
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=BRANCH_REGISTRY):
        task = FetchAppUpdatesTask(bench, tmp_path, MagicMock())
        task.run()

    app.has_remote_update.assert_called_once()
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {"hrms": True}


def test_run_app_not_in_marketplace_falls_back_to_remote_check(tmp_path: Path, capsys) -> None:
    _make_git_apps(tmp_path, ["frappe"])
    app = app_mock("https://github.com/frappe/frappe", "16.0.0", has_remote_update=False)
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=[]):
        task = FetchAppUpdatesTask(bench, tmp_path, MagicMock())
        task.run()

    app.has_remote_update.assert_called_once()
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {"frappe": False}


def test_run_mixed_apps_combine_local_and_remote_results(tmp_path: Path, capsys) -> None:
    _make_git_apps(tmp_path, ["helpdesk", "hrms"])
    apps = {
        "helpdesk": app_mock("https://github.com/frappe/helpdesk", "1.0.0", installed_tag="v1.0.0"),  # pinned, local
        "hrms": app_mock("https://github.com/frappe/hrms", "3.0.0", has_remote_update=True),  # branch, remote
    }
    bench = MagicMock()
    bench.app.side_effect = lambda n: apps[n]

    with patch.object(Marketplace, "registry", return_value=TAG_REGISTRY + BRANCH_REGISTRY):
        task = FetchAppUpdatesTask(bench, tmp_path, MagicMock())
        task.run()

    apps["helpdesk"].has_remote_update.assert_not_called()
    apps["hrms"].has_remote_update.assert_called_once()
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {"helpdesk": False, "hrms": True}


def test_run_ignores_non_git_dirs(tmp_path: Path, capsys) -> None:
    (tmp_path / "apps" / "erpnext" / ".git").mkdir(parents=True)
    (tmp_path / "apps" / "not_an_app").mkdir(parents=True)  # no .git
    app = app_mock("r", "1.0.0", has_remote_update=False)
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=[]):
        FetchAppUpdatesTask(bench, tmp_path, MagicMock()).run()

    bench.app.assert_called_once_with("erpnext")
