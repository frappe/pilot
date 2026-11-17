from __future__ import annotations

from pathlib import Path

import pytest

from pilot.commands.bench.import_legacy_bench import ImportLegacyBenchCommand
from pilot.core.server.legacy_bench import LegacyBenchImporter, LegacyImportError
from pilot.exceptions import BenchError
from tests.pilot.core.test_legacy_bench import write_pilot_server


@pytest.fixture
def benches(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "benches"
    path.mkdir()
    monkeypatch.setattr("pilot.core.server.Server.benches_dir", path)
    return path


def test_dry_run_prints_the_plan_and_changes_nothing(
    legacy_path: Path, benches: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(LegacyBenchImporter, "run", lambda self, on_progress: pytest.fail("imported"))

    ImportLegacyBenchCommand(path=legacy_path, dry_run=True).run()

    output = capsys.readouterr().out
    assert f"Plan: move {legacy_path} to {benches / 'old-bench'}" in output
    assert "Pilot supports Frappe v16 and Nightly only." in output
    assert "frappe: develop @" in output
    assert "used by neither the bench nor a site: unused" in output
    assert "Pilot has none yet. You will log in to 10.0.0.5:3306" in output
    assert "one.local -> one.local  (keeps database _db)" in output
    assert legacy_path.exists()


def test_the_plan_shows_a_copy_for_a_site_on_another_server(legacy_path: Path, benches: Path, capsys) -> None:
    write_pilot_server(benches, host="localhost")

    ImportLegacyBenchCommand(path=legacy_path, dry_run=True).run()

    assert "one.local -> one.local  (database _db is copied to localhost:3306)" in capsys.readouterr().out


def test_declining_the_prompt_changes_nothing(legacy_path: Path, benches: Path, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    monkeypatch.setattr(LegacyBenchImporter, "run", lambda self, on_progress: pytest.fail("imported"))

    with pytest.raises(Exception, match="Aborted"):
        ImportLegacyBenchCommand(path=legacy_path).run()


def test_the_login_is_asked_after_the_plan_is_confirmed(
    legacy_path: Path, benches: Path, monkeypatch
) -> None:
    answers = iter(["y", ""])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda prompt: "root-pw")
    saved = []
    monkeypatch.setattr(LegacyBenchImporter, "save_database_login", lambda self, login: saved.append(login))
    monkeypatch.setattr(LegacyBenchImporter, "run", lambda self, on_progress: pytest.fail("stop here"))

    with pytest.raises(pytest.fail.Exception, match="stop here"):
        ImportLegacyBenchCommand(path=legacy_path).run()

    assert [(login.server.label, login.user, login.password) for login in saved] == [
        ("10.0.0.5:3306", "root", "root-pw")
    ]


def test_a_missing_login_without_a_terminal_stops_before_moving(
    legacy_path: Path, benches: Path, monkeypatch
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with pytest.raises(BenchError, match="Run this in a terminal"):
        ImportLegacyBenchCommand(path=legacy_path, skip_confirm=True).run()
    assert legacy_path.exists()


def run_failing_import(legacy_path: Path, monkeypatch, answers: list[str], **command) -> list:
    reverted = []
    replies = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt: next(replies))
    monkeypatch.setattr(LegacyBenchImporter, "save_database_login", lambda self, login: None)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "pw")

    def fail(self, on_progress):
        raise LegacyImportError("install failed")

    monkeypatch.setattr(LegacyBenchImporter, "run", fail)
    monkeypatch.setattr(LegacyBenchImporter, "revert", lambda self, on_progress: reverted.append(True))
    with pytest.raises(LegacyImportError):
        ImportLegacyBenchCommand(path=legacy_path, **command).run()
    return reverted


def test_a_failed_import_is_reverted_when_the_user_says_yes(
    legacy_path: Path, benches: Path, monkeypatch
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    assert run_failing_import(legacy_path, monkeypatch, ["y", "", "y"]) == [True]


def test_a_failed_import_is_kept_when_the_user_says_no(
    legacy_path: Path, benches: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    assert run_failing_import(legacy_path, monkeypatch, ["y", "", "n"]) == []
    assert f"Kept the bench at {benches / 'old-bench'}" in capsys.readouterr().out


def test_yes_reverts_a_failed_import(legacy_path: Path, benches: Path, monkeypatch) -> None:
    write_pilot_server(benches)

    assert run_failing_import(legacy_path, monkeypatch, [], skip_confirm=True) == [True]


def test_the_summary_names_old_databases_left_behind(legacy_path: Path, benches: Path, capsys) -> None:
    from pilot.core.server.legacy_bench import ImportResult, LegacyBench, SiteImportResult

    write_pilot_server(benches, host="localhost")
    importer = LegacyBenchImporter(LegacyBench(legacy_path), benches)
    config = type("Config", (), {"name": "old-bench", "admin": type("Admin", (), {"port": 7005})()})()
    bench = type("FakeBench", (), {"config": config, "path": importer.bench_path})()
    result = ImportResult(legacy_path, bench, "pw", [SiteImportResult("one.local", "one.local", False)])

    ImportLegacyBenchCommand(path=legacy_path).report_result(importer, result)

    output = capsys.readouterr().out
    assert f"delete {importer.bench_path / '.legacy'}" in output
    assert "one.local's old database _db on 10.0.0.5:3306 is no longer used" in output
