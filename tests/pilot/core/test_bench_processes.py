from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.config import BenchConfig
from pilot.core.app.processes import AppProcessError
from pilot.core.bench.processes import BenchProcesses
from pilot.core.site.apps import SiteApps
from pilot.exceptions import BenchError
from pilot.managers.processes.definitions import ProcessDefinitionBuilder
from tests.pilot.commands.test_commands import make_bench

_MAIL = '[tool.pilot.processes.stalwart]\ncommand = ["/usr/bin/stalwart"]\n[tool.pilot.processes.relay]\ncommand = ["relay"]\n'


@pytest.fixture
def bench(tmp_path: Path, monkeypatch):
    bench = make_bench(tmp_path)
    bench.create_directories()
    bench.config.write(bench.path)
    for name, pyproject in {"mail": _MAIL, "crm": "[project]\nname = 'crm'\n"}.items():
        (bench.apps_path / name / ".git").mkdir(parents=True)
        (bench.apps_path / name / "pyproject.toml").write_text(pyproject)
    monkeypatch.setattr(BenchProcesses, "active_apps", property(lambda self: {"mail"}))
    return bench


def _running(bench) -> list[str]:
    builder = ProcessDefinitionBuilder(bench, Path("/usr/bin/python3"), watch_admin_js=False)
    return [definition.name for definition in builder.prod_process_definitions()]


def test_runs_the_processes_of_apps_a_site_uses(bench, monkeypatch) -> None:
    assert {"web", "mail-stalwart", "mail-relay"} <= set(_running(bench))

    monkeypatch.setattr(BenchProcesses, "active_apps", property(lambda self: set()))
    assert not any(name.startswith("mail-") for name in _running(bench))


def test_disable_and_enable_are_saved_and_take_an_app_name(bench) -> None:
    assert bench.processes.disable(["mail-relay"]) == ["mail-relay"]
    assert "mail-relay" not in _running(bench)
    assert BenchConfig.read(bench.path).processes.disabled == ["mail-relay"]

    assert bench.processes.enable(["mail"]) == ["mail-stalwart", "mail-relay"]
    assert bench.config.processes.disabled == []


def test_unknown_process_is_rejected(bench) -> None:
    with pytest.raises(BenchError, match="No process or app named 'crm'"):
        bench.processes.disable(["crm"])


def test_rows_say_why_a_declared_process_does_not_run(bench, monkeypatch) -> None:
    bench.processes.disable(["mail-relay"])
    monkeypatch.setattr(BenchProcesses, "active_apps", property(lambda self: set()))

    rows = {row.name: row for row in bench.processes.get_rows("mail")}

    assert rows["mail-relay"].state.state == "disabled"
    assert rows["mail-stalwart"].state.state == "no site uses the app"
    assert rows["mail-stalwart"].app == "mail"


def test_a_broken_app_does_not_block_checking_another(bench) -> None:
    (bench.apps_path / "crm" / "pyproject.toml").write_text("[tool.pilot\n")

    assert bench.processes.has_declarations(["mail"])


def test_realtime_change_time_follows_handlers_not_compiled_files(bench) -> None:
    handlers = bench.apps_path / "mail" / "mail" / "realtime"
    (handlers / "__pycache__").mkdir(parents=True)
    (handlers / "handlers.py").write_text("")
    (handlers / "__pycache__" / "handlers.pyc").write_text("")
    for path in (handlers, handlers / "handlers.py"):
        os.utime(path, (1000, 1000))

    assert bench.processes.get_realtime_change_time() == 1000


def test_removing_a_handler_counts_as_a_realtime_change(bench) -> None:
    handlers = bench.apps_path / "mail" / "realtime"
    handlers.mkdir()
    (handlers / "old.js").write_text("")
    (handlers / "kept.js").write_text("")
    for path in (handlers, handlers / "kept.js"):
        os.utime(path, (1000, 1000))

    (handlers / "old.js").unlink()

    assert bench.processes.get_realtime_change_time() > 1000


@pytest.mark.parametrize("action", ["install_app", "enable_app", "disable_app", "uninstall_app"])
def test_site_app_changes_apply_the_process_set_for_apps_that_declare_one(bench, action) -> None:
    site = bench.site("site.local")
    for app_name, applies in (("mail", True), ("crm", False)):
        with (
            patch.object(SiteApps, action),
            patch.object(SiteApps, "get_required_apps", return_value=[]),
            patch.object(BenchProcesses, "apply") as apply,
        ):
            getattr(site, action)(bench.app(app_name))
        assert apply.called is applies


def test_removing_a_handler_folder_counts_as_a_realtime_change(bench) -> None:
    handlers = bench.apps_path / "mail" / "realtime"
    handlers.mkdir()
    (handlers / "handlers.js").write_text("")
    os.utime(handlers / "handlers.js", (1000, 1000))
    os.utime(handlers, (1000, 1000))
    bench.config_path.mkdir(parents=True, exist_ok=True)
    assert not bench.processes.is_realtime_changed_since(2000)

    bench.processes.save_realtime_folders()

    (handlers / "handlers.js").unlink()
    handlers.rmdir()

    # Until a reload saves the folders socketio restarted with, the removal still counts.
    assert bench.processes.is_realtime_changed_since(2000)
    assert bench.processes.is_realtime_changed_since(2000)
    bench.processes.save_realtime_folders()
    assert not bench.processes.is_realtime_changed_since(2000)


@pytest.mark.parametrize("action", ["install_app", "enable_app"])
def test_an_app_change_applies_the_processes_of_its_dependencies(bench, action) -> None:
    (bench.apps_path / "helpdesk" / ".git").mkdir(parents=True)
    site = bench.site("site.local")
    required = {"crm": ["helpdesk"], "helpdesk": ["mail"], "mail": []}
    with (
        patch.object(SiteApps, action),
        patch.object(SiteApps, "get_required_apps", side_effect=lambda app: required[app.config.name]),
        patch.object(type(bench), "is_app_installed", return_value=True),
        patch.object(BenchProcesses, "apply") as apply,
    ):
        getattr(site, action)(bench.app("crm"))

    apply.assert_called_once_with()


def test_a_broken_declaration_blocks_only_the_apps_sites_use(bench, monkeypatch) -> None:
    (bench.apps_path / "crm" / "pyproject.toml").write_text("[tool.pilot.processes.desk]\ncommand = 1\n")

    assert {d.name for d in bench.processes.get_app_definitions()} == {"mail-stalwart", "mail-relay"}
    # Counted as declaring processes, so a removal goes ahead and the apply after it reports the error.
    assert bench.processes.has_declarations(["crm"])

    monkeypatch.setattr(BenchProcesses, "active_apps", property(lambda self: {"mail", "crm"}))
    with pytest.raises(AppProcessError, match="App 'crm'"):
        bench.processes.get_app_definitions()


@pytest.mark.parametrize(("parts", "applies"), [(["database"], True), (["public", "config"], False)])
def test_a_database_restore_applies_the_process_set(bench, parts, applies) -> None:
    from pilot.core.site.restore import SiteRestore

    with patch.object(SiteRestore, "restore"), patch.object(BenchProcesses, "apply") as apply:
        bench.site("site.local").restore_backup(MagicMock(), parts)

    assert apply.called is applies


def test_a_site_made_from_a_backup_applies_the_process_set(bench) -> None:
    from pilot.core.site import Site, provision_from_backup

    calls = []
    with (
        patch.object(Site, "provision", return_value=MagicMock()),
        patch.object(SiteApps, "restore_installed_apps_copy", side_effect=lambda: calls.append("refresh")),
        patch.object(BenchProcesses, "apply", side_effect=lambda: calls.append("apply")),
    ):
        provision_from_backup(bench, "site.local", "db.sql.gz", "admin")

    # The saved app list comes from the restored database first, or a new app's processes stay off.
    assert calls == ["refresh", "apply"]
