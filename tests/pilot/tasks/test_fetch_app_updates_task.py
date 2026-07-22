from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.core.app import RevisionPin
from pilot.integrations.marketplace import Marketplace
from pilot.tasks.fetch_app_updates import FetchAppUpdatesTask

REGISTRY = [{"name": "helpdesk", "repo": "r", "targets": []}]


def _make_git_apps(bench_root: Path, names: list[str]) -> None:
    for name in names:
        (bench_root / "apps" / name / ".git").mkdir(parents=True)


def _app(pin: RevisionPin | None, *, on_revision: bool = False, head: str = "1111111111aaaa") -> MagicMock:
    app = MagicMock()
    app.update_target.return_value = pin
    app.is_on_revision.return_value = on_revision
    app.installed_hash = head
    return app


def test_run_reports_current_and_target_for_pending_update(tmp_path: Path, capsys) -> None:
    _make_git_apps(tmp_path, ["helpdesk"])
    app = _app(RevisionPin(kind="commit", ref="2222222222bbbb"), head="1111111111aaaa")
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=REGISTRY):
        FetchAppUpdatesTask(bench=bench, bench_root=tmp_path).run()

    app.update_target.assert_called_once_with(REGISTRY[0])
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {"helpdesk": {"current": "1111111111", "target": "2222222222"}}


def test_run_omits_apps_that_are_up_to_date(tmp_path: Path, capsys) -> None:
    _make_git_apps(tmp_path, ["frappe"])
    app = _app(RevisionPin(kind="commit", ref="abc"), on_revision=True)
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=[]):
        FetchAppUpdatesTask(bench=bench, bench_root=tmp_path).run()

    app.update_target.assert_called_once_with(None)
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {}


def test_run_shows_tag_ref_when_target_is_a_tag(tmp_path: Path, capsys) -> None:
    _make_git_apps(tmp_path, ["helpdesk"])
    app = _app(RevisionPin(kind="tag", ref="v15.1.0"))
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=REGISTRY):
        FetchAppUpdatesTask(bench=bench, bench_root=tmp_path).run()

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["helpdesk"]["target"] == "v15.1.0"


def _marketplace_app(name: str, repo: str, branch: str, version: str) -> MagicMock:
    app = _app(RevisionPin(kind="commit", ref="2222222222bbbb"), head="1111111111aaaa")
    app.config.name = name
    app.config.repo = repo
    app.config.branch = branch
    app.installed_version = version
    return app


ERP_NEXT_REGISTRY = [
    {
        "name": "erpnext",
        "repo": "https://github.com/frappe/erpnext",
        "targets": [
            {"version": "16.28.0", "target_type": "branch", "target": "version-16"},
            {"version": "15.117.0", "target_type": "branch", "target": "version-15"},
        ],
    }
]


def test_run_uses_marketplace_versions_for_marketplace_apps(tmp_path: Path, capsys) -> None:
    _make_git_apps(tmp_path, ["erpnext"])
    # A .git suffix or embedded credentials still match the registry repo.
    app = _marketplace_app(
        "erpnext", "https://token:x@github.com/frappe/erpnext.git", "version-15", "15.116.0"
    )
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=ERP_NEXT_REGISTRY):
        FetchAppUpdatesTask(bench=bench, bench_root=tmp_path).run()

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {"erpnext": {"current": "15.116.0", "target": "15.117.0"}}


def test_run_keeps_commit_labels_without_a_matching_marketplace_line(tmp_path: Path, capsys) -> None:
    _make_git_apps(tmp_path, ["erpnext"])
    app = _marketplace_app(
        "erpnext", "https://github.com/frappe/erpnext", "develop", "17.0.0-dev"
    )
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=ERP_NEXT_REGISTRY):
        FetchAppUpdatesTask(bench=bench, bench_root=tmp_path).run()

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {"erpnext": {"current": "1111111111", "target": "2222222222"}}


def test_run_ignores_non_git_dirs(tmp_path: Path) -> None:
    (tmp_path / "apps" / "erpnext" / ".git").mkdir(parents=True)
    (tmp_path / "apps" / "not_an_app").mkdir(parents=True)  # no .git
    app = _app(None, on_revision=True)
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=[]):
        FetchAppUpdatesTask(bench=bench, bench_root=tmp_path).run()

    bench.app.assert_called_once_with("erpnext")


def test_run_mixed_apps_combine_results(tmp_path: Path, capsys) -> None:
    _make_git_apps(tmp_path, ["helpdesk", "hrms"])
    apps = {
        "helpdesk": _app(RevisionPin(kind="commit", ref="deadbeef00"), on_revision=True),
        "hrms": _app(RevisionPin(kind="commit", ref="2222222222bbbb"), head="1111111111aaaa"),
    }
    bench = MagicMock()
    bench.app.side_effect = lambda n: apps[n]

    with patch.object(Marketplace, "registry", return_value=[]):
        FetchAppUpdatesTask(bench=bench, bench_root=tmp_path).run()

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {"hrms": {"current": "1111111111", "target": "2222222222"}}
