from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from pilot.config.common import CommonConfig
from pilot.core.app import App
from pilot.core.server import Server
from pilot.core.server.legacy_bench import DatabaseLogin, LegacyBench, LegacyBenchImporter, LegacyImportError
from pilot.core.server.legacy_bench import importer as importer_module
from pilot.core.server.legacy_bench.database import DatabaseServer, get_client_options
from pilot.core.server.legacy_bench.importer import (
    get_available_name,
    get_suffixed_site_name,
    get_valid_bench_name,
    validate_legacy_apps,
)
from pilot.core.server.legacy_bench.journal import MoveJournal
from pilot.core.server.legacy_bench.site_import import LegacySiteImport
from pilot.exceptions import AppValidationError, BenchError
from pilot.managers.redis import RedisManager
from tests.pilot.legacy_bench import make_app, make_site


def test_site_names_take_a_suffix_before_the_tld() -> None:
    assert get_suffixed_site_name("test1.local", 1) == "test1-1.local"
    assert get_suffixed_site_name("erp.example.com", 2) == "erp-2.example.com"
    assert get_suffixed_site_name("localhost", 1) == "localhost-1"


def test_available_name_counts_up_until_free() -> None:
    taken = {"a", "a-1"}
    assert get_available_name("a", taken.__contains__, lambda base, n: f"{base}-{n}") == "a-2"
    assert get_available_name("b", taken.__contains__, lambda base, n: f"{base}-{n}") == "b"


def test_bench_names_are_made_valid() -> None:
    assert get_valid_bench_name("my bench") == "my-bench"
    assert get_valid_bench_name("2024") == "bench-2024"
    assert get_valid_bench_name("bench2") == "bench2"


def test_reader_orders_apps_and_reports_skipped_and_missing(legacy_path: Path) -> None:
    legacy = LegacyBench(legacy_path)

    assert [app.name for app in legacy.apps] == ["frappe", "erpnext"]
    assert legacy.skipped_apps == ["unused"]
    assert legacy.missing_apps == ["gone"]
    assert [site.name for site in legacy.sites] == ["one.local"]
    assert legacy.framework_app.branch == "develop"


def test_reader_drops_credentials_from_the_repo(tmp_path: Path) -> None:
    bench = tmp_path / "b"
    make_app(bench, "frappe", "https://x-access-token:ghs_abc@github.com/org/frappe", remote_name="origin")
    (bench / "sites").mkdir()
    app = LegacyBench(bench).framework_app

    assert app.repo == "https://github.com/org/frappe"
    assert app.has_credentials_in_remote


def test_site_database_server_falls_back_to_common_config(legacy_path: Path) -> None:
    site = LegacyBench(legacy_path).sites[0]

    assert site.database_server == DatabaseServer("10.0.0.5", 3306)
    assert site.database_user == "_db"


def test_reader_refuses_a_folder_that_is_not_a_legacy_bench(tmp_path: Path, legacy_path: Path) -> None:
    with pytest.raises(BenchError, match="is not a bench"):
        LegacyBench(tmp_path)
    (legacy_path / "bench.toml").write_text("")
    with pytest.raises(BenchError, match="already a Pilot bench"):
        LegacyBench(legacy_path)


def test_importer_picks_a_free_bench_name(tmp_path: Path, legacy_path: Path) -> None:
    benches = tmp_path / "benches"
    (benches / "old-bench").mkdir(parents=True)
    (benches / "old-bench-1").mkdir()

    assert LegacyBenchImporter(LegacyBench(legacy_path), benches).name == "old-bench-2"
    assert LegacyBenchImporter(LegacyBench(legacy_path), benches, "pilot").name == "pilot-1"


def test_importer_renames_sites_another_bench_claims(tmp_path: Path, legacy_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "pilot.core.bench.directory.BenchDirectory.get_host_owner",
        lambda self, host: "other" if host == "one.local" else None,
    )
    importer = LegacyBenchImporter(LegacyBench(legacy_path), tmp_path / "benches")

    assert [plan.name for plan in importer.site_plans] == ["one-1.local"]


def test_server_builds_the_importer(legacy_path: Path, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Server, "benches_dir", tmp_path / "benches")

    importer = Server().get_legacy_bench_importer(legacy_path, "fresh")

    assert importer.bench_path == tmp_path / "benches" / "fresh"


def test_source_checks_stop_an_invalid_app_before_anything_is_created(legacy_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(App, "is_marketplace", property(lambda self: False))

    with pytest.raises(AppValidationError, match=r"pyproject\.toml"):
        validate_legacy_apps(LegacyBench(legacy_path), lambda message: None)


def write_pilot_server(benches: Path, host: str = "10.0.0.5", port: int = 3306) -> None:
    with CommonConfig.open(benches) as common:
        common.mariadb.host, common.mariadb.port, common.mariadb.root_password = host, port, "root-secret"


def make_importer(legacy_path: Path, tmp_path: Path) -> LegacyBenchImporter:
    benches = tmp_path / "benches"
    benches.mkdir(exist_ok=True)
    return LegacyBenchImporter(LegacyBench(legacy_path), benches)


def test_servers_match_by_port_and_host_with_local_names_alike() -> None:
    assert DatabaseServer("localhost", 3306).is_same_as(DatabaseServer("127.0.0.1", 3306))
    assert not DatabaseServer("localhost", 3306).is_same_as(DatabaseServer("localhost", 3307))
    assert not DatabaseServer("db1", 3306).is_same_as(DatabaseServer("db2", 3306))
    assert not DatabaseServer("localhost", 3306, "/run/mysqld.sock").is_same_as(
        DatabaseServer("localhost", 3306)
    )


def test_a_site_on_pilots_server_keeps_its_database(legacy_path: Path, tmp_path: Path) -> None:
    importer = make_importer(legacy_path, tmp_path)
    write_pilot_server(importer.benches_dir)

    assert not importer.needs_database_login
    assert [plan.keeps_database for plan in importer.site_plans] == [True]


def test_a_site_on_another_server_gets_a_copy(legacy_path: Path, tmp_path: Path) -> None:
    importer = make_importer(legacy_path, tmp_path)
    write_pilot_server(importer.benches_dir, host="localhost")

    assert [plan.keeps_database for plan in importer.site_plans] == [False]


def test_without_a_pilot_server_the_old_server_becomes_it(
    legacy_path: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("pilot.core.server.legacy_bench.database.check_login", lambda *args: None)
    importer = make_importer(legacy_path, tmp_path)

    assert importer.needs_database_login
    assert [plan.keeps_database for plan in importer.site_plans] == [True]
    importer.save_database_login(DatabaseLogin(importer.pilot_server, "admin", "pw"))

    mariadb = CommonConfig.read(importer.benches_dir).mariadb
    assert (mariadb.host, mariadb.port, mariadb.admin_user, mariadb.root_password) == (
        "10.0.0.5",
        3306,
        "admin",
        "pw",
    )
    assert mariadb.existing
    assert not importer.needs_database_login


def test_a_site_on_another_engine_is_refused(legacy_path: Path, tmp_path: Path) -> None:
    make_site(legacy_path, "pg.local", db_type="postgres")
    importer = make_importer(legacy_path, tmp_path)
    write_pilot_server(importer.benches_dir)

    with pytest.raises(BenchError, match=r"pg\.local uses postgres"):
        importer.validate_sites()


def test_a_postgres_site_off_pilots_server_is_refused(legacy_path: Path, tmp_path: Path) -> None:
    (legacy_path / "sites" / "one.local" / "site_config.json").write_text(
        json.dumps({"db_name": "_db", "db_type": "postgres"})
    )
    importer = make_importer(legacy_path, tmp_path)
    with CommonConfig.open(importer.benches_dir) as common:
        common.postgres.root_password = "pg-secret"

    assert importer.engine == "postgres"
    with pytest.raises(BenchError, match="cannot copy PostgreSQL"):
        importer.validate_sites()


def test_client_options_quote_the_password() -> None:
    options = get_client_options(DatabaseServer("localhost", 3306), "u", 'p"w\\')

    assert 'password="p\\"w\\\\"' in options
    assert "port=3306" in options


def test_journal_undo_puts_files_back_over_what_replaced_them(tmp_path: Path) -> None:
    (tmp_path / "env").mkdir()
    (tmp_path / "env" / "old").write_text("old")
    journal = MoveJournal()
    journal.move(tmp_path / "env", tmp_path / ".legacy" / "env")
    (tmp_path / "env").mkdir()
    (tmp_path / "env" / "new").write_text("new")

    journal.undo()

    assert [path.name for path in (tmp_path / "env").iterdir()] == ["old"]
    assert not (tmp_path / ".legacy" / "env").exists()


class FakeBench:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.apps_path = path / "apps"
        self.config = type("Config", (), {"redis": None})()

    def initialize(self, on_progress) -> None:
        (self.path / "env").mkdir()
        (self.path / "sites" / "common_site_config.json").write_text("{}")


def get_tree(path: Path) -> list[str]:
    return sorted(str(item.relative_to(path)) for item in path.rglob("*") if ".git" not in item.parts)


def run_with_fakes(importer: LegacyBenchImporter, monkeypatch, site_run) -> None:
    write_pilot_server(importer.benches_dir)
    monkeypatch.setattr(importer_module, "validate_legacy_apps", lambda legacy, on_progress: None)

    def create_bench(framework, on_progress):
        (importer.bench_path / "bench.toml").write_text("")
        return FakeBench(importer.bench_path), "pw"

    monkeypatch.setattr(importer, "create_bench", create_bench)
    monkeypatch.setattr(importer, "install_app", lambda bench, config, on_progress: None)
    monkeypatch.setattr(RedisManager, "running", lambda self: nullcontext())
    monkeypatch.setattr(LegacySiteImport, "run", site_run)


def test_the_folder_moves_and_revert_puts_it_back(legacy_path: Path, tmp_path: Path, monkeypatch) -> None:
    before = get_tree(legacy_path)
    importer = make_importer(legacy_path, tmp_path)

    def site_run(self, on_progress):
        raise BenchError("migrate failed")

    run_with_fakes(importer, monkeypatch, site_run)

    result = importer.run(lambda message: None)

    assert not legacy_path.exists()
    assert (importer.bench_path / ".legacy" / "env").exists() is False
    assert (importer.bench_path / ".legacy" / "apps" / "unused").is_dir()
    assert (importer.bench_path / "apps" / "erpnext").is_dir()
    assert (importer.bench_path / ".legacy" / "sites" / "one.local" / "site_config.json").exists()
    assert [(site.name, site.error) for site in result.sites] == [("one.local", "migrate failed")]

    importer.revert(lambda message: None)

    assert not importer.bench_path.exists()
    assert get_tree(legacy_path) == before


def test_a_failure_while_rebuilding_is_reported_for_revert(
    legacy_path: Path, tmp_path: Path, monkeypatch
) -> None:
    importer = make_importer(legacy_path, tmp_path)
    run_with_fakes(importer, monkeypatch, lambda self, on_progress: None)
    monkeypatch.setattr(
        FakeBench, "initialize", lambda self, on_progress: (_ for _ in ()).throw(BenchError("no pip"))
    )

    with pytest.raises(LegacyImportError, match="no pip"):
        importer.run(lambda message: None)
    importer.revert(lambda message: None)

    assert (legacy_path / "apps" / "unused").is_dir()
    assert not (legacy_path / "bench.toml").exists()
