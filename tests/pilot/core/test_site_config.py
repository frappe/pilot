from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.database.base import QueryResult
from pilot.core.database.engines import MariaDB, PostgreSQL
from pilot.core.site.config import database_state
from pilot.core.site.config.database_state import SiteDatabaseState
from pilot.exceptions import DatabaseError

_BENCH_ROOT = Path("/bench")


def _mariadb() -> MariaDB:
    return MariaDB(host="localhost", port=3306, user="u", password="p", database="site_db")


def _postgres() -> PostgreSQL:
    return PostgreSQL(host="localhost", port=5432, user="u", password="p", database="site_db")


def _make_sqlite_site(
    bench_root: Path,
    site_name: str,
    setup_complete: str,
    apps: list[str],
    disabled: list[str] | None = None,
) -> None:
    """A real SQLite site, so the probe runs against an actual Frappe-shaped schema.
    `disabled=None` models a Frappe old enough to have no `disabled` field."""
    site_path = bench_root / "sites" / site_name
    (site_path / "db").mkdir(parents=True)
    (site_path / "site_config.json").write_text(json.dumps({"db_type": "sqlite", "db_name": "site_db"}))

    connection = sqlite3.connect(site_path / "db" / "site_db.db")
    with connection:
        connection.execute("CREATE TABLE `tabSingles` (doctype TEXT, field TEXT, value TEXT)")
        connection.execute(
            "INSERT INTO `tabSingles` VALUES ('System Settings', 'setup_complete', ?)",
            (setup_complete,),
        )
        connection.execute("CREATE TABLE `tabDocField` (parent TEXT, fieldname TEXT)")
        connection.execute("CREATE TABLE `tabDefaultValue` (parent TEXT, defkey TEXT, defvalue TEXT)")
        connection.execute("CREATE TABLE `tabInstalled Application` (app_name TEXT, idx INTEGER)")
        connection.executemany(
            "INSERT INTO `tabInstalled Application` VALUES (?, ?)",
            [(app, index) for index, app in enumerate(apps)],
        )
        if disabled is not None:
            connection.execute("INSERT INTO `tabDocField` VALUES ('Installed Application', 'disabled')")
            connection.execute(
                "INSERT INTO `tabDefaultValue` VALUES ('__global', 'disabled_apps', ?)",
                (json.dumps(disabled),),
            )
    connection.close()


def _stub_database(
    monkeypatch: pytest.MonkeyPatch, engine, rows: list[list], only_for: str | None = None
) -> list[str]:
    """Keep the real engine so identifier quoting is exercised, stub the wire. `only_for`
    makes every other query fail, modelling a table or column this Frappe lacks."""
    executed: list[str] = []

    def execute(self, query: str, read_only: bool = True) -> QueryResult:
        executed.append(query)
        if only_for and only_for not in query:
            raise DatabaseError("no such column")
        return QueryResult(columns=["value"], rows=rows, duration_ms=0.0)

    monkeypatch.setattr(type(engine), "execute", execute)
    monkeypatch.setattr(database_state, "make_site_database", lambda *args, **kwargs: engine)
    return executed


def test_is_setup_complete_true_on_mariadb(monkeypatch: pytest.MonkeyPatch) -> None:
    executed = _stub_database(monkeypatch, _mariadb(), [["1"]], only_for="tabSingles")

    assert SiteDatabaseState(_BENCH_ROOT, "site.localhost").is_setup_complete is True
    assert any("`tabSingles`" in query for query in executed)


def test_is_setup_complete_false_on_mariadb(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_database(monkeypatch, _mariadb(), [["0"]], only_for="tabSingles")

    assert SiteDatabaseState(_BENCH_ROOT, "site.localhost").is_setup_complete is False


def test_is_setup_complete_true_on_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    executed = _stub_database(monkeypatch, _postgres(), [["1"]], only_for="tabSingles")

    assert SiteDatabaseState(_BENCH_ROOT, "postgres.localhost").is_setup_complete is True
    assert any('"tabSingles"' in query for query in executed)
    assert not any("`" in query for query in executed)


@pytest.mark.parametrize("stored", ["true", "TRUE", " true "])
def test_is_setup_complete_accepts_postgres_boolean_spelling(
    monkeypatch: pytest.MonkeyPatch, stored: str
) -> None:
    """set_single_value writes a Python bool: psycopg2 renders it 'true', mysqlclient '1'."""
    _stub_database(monkeypatch, _postgres(), [[stored]], only_for="tabSingles")

    assert SiteDatabaseState(_BENCH_ROOT, "postgres.localhost").is_setup_complete is True


@pytest.mark.parametrize("stored", ["0", "false", "", "t", "yes"])
def test_is_setup_complete_rejects_other_values(monkeypatch: pytest.MonkeyPatch, stored: str) -> None:
    _stub_database(monkeypatch, _postgres(), [[stored]], only_for="tabSingles")

    assert SiteDatabaseState(_BENCH_ROOT, "postgres.localhost").is_setup_complete is False


def test_is_setup_complete_false_when_setting_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_database(monkeypatch, _postgres(), [], only_for="tabSingles")

    assert SiteDatabaseState(_BENCH_ROOT, "postgres.localhost").is_setup_complete is False


def test_is_setup_complete_none_when_database_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(*args, **kwargs):
        raise DatabaseError("connection refused")

    monkeypatch.setattr(database_state, "make_site_database", unreachable)

    assert SiteDatabaseState(_BENCH_ROOT, "site.localhost").is_setup_complete is None


def test_is_setup_complete_none_when_site_config_missing(tmp_path: Path) -> None:
    assert SiteDatabaseState(tmp_path, "missing.localhost").is_setup_complete is None


def test_query_installed_apps_via_db_on_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    executed = _stub_database(monkeypatch, _postgres(), [["frappe"], ["erpnext"]])

    apps = SiteDatabaseState(_BENCH_ROOT, "postgres.localhost").installed_apps

    assert apps == ["frappe", "erpnext"]
    assert '"tabInstalled Application"' in executed[0]


def test_is_setup_complete_true_on_sqlite(tmp_path: Path) -> None:
    """SQLite sites have no db_password, which used to short-circuit the probe."""
    _make_sqlite_site(tmp_path, "sqlite.localhost", "1", ["frappe"])

    assert SiteDatabaseState(tmp_path, "sqlite.localhost").is_setup_complete is True


def test_is_setup_complete_false_on_sqlite(tmp_path: Path) -> None:
    _make_sqlite_site(tmp_path, "sqlite.localhost", "0", ["frappe"])

    assert SiteDatabaseState(tmp_path, "sqlite.localhost").is_setup_complete is False


def test_query_installed_apps_via_db_on_sqlite(tmp_path: Path) -> None:
    _make_sqlite_site(tmp_path, "sqlite.localhost", "1", ["frappe", "erpnext"])

    apps = SiteDatabaseState(tmp_path, "sqlite.localhost").installed_apps

    assert apps == ["frappe", "erpnext"]


def test_query_installed_apps_via_db_none_when_database_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreachable(*args, **kwargs):
        raise DatabaseError("connection refused")

    monkeypatch.setattr(database_state, "make_site_database", unreachable)

    assert SiteDatabaseState(_BENCH_ROOT, "site.localhost").installed_apps is None


def test_query_disabled_apps_reads_the_disabled_apps_global(tmp_path: Path) -> None:
    _make_sqlite_site(tmp_path, "sqlite.localhost", "1", ["frappe", "erpnext"], disabled=["erpnext"])

    assert SiteDatabaseState(tmp_path, "sqlite.localhost").disabled_apps == ["erpnext"]


def test_has_app_disabling_follows_the_installed_application_docfield(tmp_path: Path) -> None:
    _make_sqlite_site(tmp_path, "sqlite.localhost", "1", ["frappe"], disabled=[])

    assert SiteDatabaseState(tmp_path, "sqlite.localhost").has_app_disabling is True


def test_has_app_disabling_false_without_the_docfield(tmp_path: Path) -> None:
    _make_sqlite_site(tmp_path, "sqlite.localhost", "1", ["frappe"])

    assert SiteDatabaseState(tmp_path, "sqlite.localhost").has_app_disabling is False


def test_exclude_disabled_drops_disabled_apps(tmp_path: Path) -> None:
    """A disabled app stays installed on the site, but is not one the site runs."""
    _make_sqlite_site(tmp_path, "sqlite.localhost", "1", ["frappe", "erpnext"], disabled=["erpnext"])
    state = SiteDatabaseState(tmp_path, "sqlite.localhost")

    assert state.installed_apps == ["frappe", "erpnext"]
    assert state.exclude_disabled(["frappe", "erpnext"]) == ["frappe"]


def test_exclude_disabled_keeps_everything_on_a_frappe_without_the_disabled_column(
    tmp_path: Path,
) -> None:
    _make_sqlite_site(tmp_path, "sqlite.localhost", "1", ["frappe", "erpnext"])
    state = SiteDatabaseState(tmp_path, "sqlite.localhost")

    assert state.exclude_disabled(["frappe", "erpnext"]) == ["frappe", "erpnext"]


def test_the_frappe_app_list_fallback_runs_the_bench_python_with_its_safe_argv(tmp_path: Path) -> None:
    from tests.pilot.commands.test_commands import make_bench

    bench = make_bench(tmp_path)
    bench.env_path.mkdir(parents=True)
    (bench.env_path / "pyvenv.cfg").write_text("version_info = 3.14.2\n")
    site = bench.site("site1")
    site.path.mkdir(parents=True)
    (site.path / "site_config.json").write_text("{}")
    with (
        patch.object(SiteDatabaseState, "installed_apps", None),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = "frappe 16.0.0\n"
        assert site.installed_apps() == ["frappe"]

    assert run.call_args.args[0][:2] == [str(bench.python), "-P"]
    assert run.call_args.kwargs["cwd"] == str(bench.sites_path)


def _make_wizard_site(bench_root: Path, site_name: str, wizard_apps: dict[str, int], disabled: list[str]) -> None:
    """A site whose Frappe tracks setup per app, the way v16 does."""
    site_path = bench_root / "sites" / site_name
    (site_path / "db").mkdir(parents=True)
    (site_path / "site_config.json").write_text(json.dumps({"db_type": "sqlite", "db_name": "site_db"}))

    connection = sqlite3.connect(site_path / "db" / "site_db.db")
    with connection:
        connection.execute("CREATE TABLE `tabSingles` (doctype TEXT, field TEXT, value TEXT)")
        connection.execute("INSERT INTO `tabSingles` VALUES ('System Settings', 'setup_complete', '1')")
        connection.execute("CREATE TABLE `tabDefaultValue` (parent TEXT, defkey TEXT, defvalue TEXT)")
        connection.execute(
            "INSERT INTO `tabDefaultValue` VALUES ('__global', 'disabled_apps', ?)", (json.dumps(disabled),)
        )
        connection.execute(
            "CREATE TABLE `tabInstalled Application` "
            "(app_name TEXT, idx INTEGER, has_setup_wizard INTEGER, is_setup_complete INTEGER)"
        )
        connection.executemany(
            "INSERT INTO `tabInstalled Application` VALUES (?, ?, 1, ?)",
            [(app, index, done) for index, (app, done) in enumerate(wizard_apps.items())],
        )
    connection.close()


def test_is_setup_complete_false_while_an_app_wizard_is_pending(tmp_path: Path) -> None:
    """The System Settings flag still says complete here - the per-app table is what counts."""
    _make_wizard_site(tmp_path, "s.localhost", {"frappe": 1, "erpnext": 0}, disabled=[])

    assert SiteDatabaseState(tmp_path, "s.localhost").is_setup_complete is False


def test_is_setup_complete_ignores_a_disabled_apps_wizard(tmp_path: Path) -> None:
    """A disabled app contributes no wizard stages, so it can never finish one - holding
    setup open on its behalf would leave the user an empty wizard they cannot complete."""
    _make_wizard_site(tmp_path, "s.localhost", {"frappe": 1, "erpnext": 0}, disabled=["erpnext"])

    assert SiteDatabaseState(tmp_path, "s.localhost").is_setup_complete is True


def test_is_setup_complete_true_when_every_wizard_is_done(tmp_path: Path) -> None:
    _make_wizard_site(tmp_path, "s.localhost", {"frappe": 1, "erpnext": 1}, disabled=[])

    assert SiteDatabaseState(tmp_path, "s.localhost").is_setup_complete is True
