from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.integrations.marketplace import Marketplace
from pilot.tasks.fetch_app_updates import FetchAppUpdatesTask

REGISTRY = [{"name": "helpdesk", "repo": "r", "targets": []}]


def _make_git_apps(bench_root: Path, names: list[str]) -> None:
    for name in names:
        (bench_root / "apps" / name / ".git").mkdir(parents=True)


def test_run_reports_has_marketplace_update_result_per_app(tmp_path: Path, capsys) -> None:
    _make_git_apps(tmp_path, ["helpdesk"])
    app = MagicMock()
    app.has_marketplace_update.return_value = True
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=REGISTRY):
        FetchAppUpdatesTask(bench=bench, bench_root=tmp_path).run()

    app.has_marketplace_update.assert_called_once_with(REGISTRY[0])
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {"helpdesk": True}


def test_run_passes_none_for_app_not_in_marketplace(tmp_path: Path, capsys) -> None:
    _make_git_apps(tmp_path, ["frappe"])
    app = MagicMock()
    app.has_marketplace_update.return_value = False
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=[]):
        FetchAppUpdatesTask(bench=bench, bench_root=tmp_path).run()

    app.has_marketplace_update.assert_called_once_with(None)
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {"frappe": False}


def test_run_ignores_non_git_dirs(tmp_path: Path) -> None:
    (tmp_path / "apps" / "erpnext" / ".git").mkdir(parents=True)
    (tmp_path / "apps" / "not_an_app").mkdir(parents=True)  # no .git
    app = MagicMock()
    app.has_marketplace_update.return_value = False
    bench = MagicMock()
    bench.app.side_effect = lambda n: app

    with patch.object(Marketplace, "registry", return_value=[]):
        FetchAppUpdatesTask(bench=bench, bench_root=tmp_path).run()

    bench.app.assert_called_once_with("erpnext")


def test_run_mixed_apps_combine_results(tmp_path: Path, capsys) -> None:
    _make_git_apps(tmp_path, ["helpdesk", "hrms"])
    apps = {"helpdesk": MagicMock(), "hrms": MagicMock()}
    apps["helpdesk"].has_marketplace_update.return_value = False
    apps["hrms"].has_marketplace_update.return_value = True
    bench = MagicMock()
    bench.app.side_effect = lambda n: apps[n]

    with patch.object(Marketplace, "registry", return_value=[]):
        FetchAppUpdatesTask(bench=bench, bench_root=tmp_path).run()

    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result == {"helpdesk": False, "hrms": True}
