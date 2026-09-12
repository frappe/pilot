"""Unit tests for Pilot command classes."""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import ANY, MagicMock, PropertyMock, patch

import pytest

from pilot.config import (
    AppConfig,
    BenchConfig,
    MariaDBConfig,
    PostgresConfig,
    RedisConfig,
    WorkerConfig,
    WorkerGroup,
)
from pilot.config.common import CommonConfig
from pilot.core.bench import Bench
from pilot.exceptions import BenchAlreadyExistsError, BenchError


def make_bench(tmp_path: Path) -> Bench:
    config = BenchConfig(
        name="test-bench",
        python_version="3.14",
        apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(
            groups=[
                WorkerGroup(queues=["default"], count=2),
                WorkerGroup(queues=["short"], count=1),
                WorkerGroup(queues=["long"], count=1),
            ]
        ),
    )
    return Bench(config, tmp_path)


def _ensure_database_credentials(bench_dir: Path) -> None:
    """The DB-credential step of `pilot init` (see BenchInitializer) - database
    ports/passwords are generated there, not by `pilot new`."""
    from pilot.core.bench import Bench
    from pilot.core.bench.initializer import BenchInitializer

    BenchInitializer(Bench(bench_dir))._ensure_database_credentials()



def _write_installable_app(app_dir: Path, name: str) -> None:
    """The minimum an app needs to pass validation, which every update runs."""
    (app_dir / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\n\n'
        '[tool.bench.frappe-dependencies]\nfrappe = ">=16.0.0,<17.0.0"\n'
    )
    (app_dir / name).mkdir(exist_ok=True)
    (app_dir / name / "__init__.py").write_text("")
    (app_dir / name / "hooks.py").write_text(f"app_name = '{name}'\n")

def test_new_command_creates_directory_and_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pilot.commands.bench.create import NewCommand

    monkeypatch.setattr("builtins.input", lambda _: "")
    target = tmp_path / "benches" / "my-bench"
    NewCommand(target_directory=target, bench_name="my-bench").run()

    assert target.is_dir()
    content = (target / "bench.toml").read_text()
    assert 'name = "my-bench"' in content


def test_new_command_raises_if_bench_already_exists(tmp_path: Path) -> None:
    from pilot.commands.bench.create import NewCommand

    target = tmp_path / "benches" / "my-bench"
    target.mkdir(parents=True)
    (target / "bench.toml").write_text("[bench]\n")

    with pytest.raises(BenchAlreadyExistsError, match="already exists"):
        NewCommand(target_directory=target, bench_name="my-bench").run()


def test_new_command_creates_benches_dir_if_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pilot.commands.bench.create import NewCommand

    monkeypatch.setattr("builtins.input", lambda _: "")
    target = tmp_path / "benches" / "fresh"
    assert not target.parent.exists()
    NewCommand(target_directory=target, bench_name="fresh").run()
    assert target.parent.is_dir()


def test_new_command_first_bench_uses_default_ports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pilot.commands.bench.create import NewCommand
    from pilot.core.bench.creator import BenchCreator

    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(BenchCreator, "_port_is_live", staticmethod(lambda port: False))
    target = tmp_path / "benches" / "my-bench"
    NewCommand(target_directory=target, bench_name="my-bench").run()

    with open(target / "bench.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["bench"]["http_port"] == 8000
    assert data["admin"]["port"] == 7000


def test_new_command_second_bench_gets_next_offset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every port field must shift by the same offset - a regression guard
    for a bug where admin_port got the offset applied twice."""
    from pilot.commands.bench.create import NewCommand
    from pilot.core.bench.creator import BenchCreator

    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(BenchCreator, "_port_is_live", staticmethod(lambda port: False))
    benches_dir = tmp_path / "benches"
    NewCommand(target_directory=benches_dir / "first", bench_name="first").run()
    NewCommand(target_directory=benches_dir / "second", bench_name="second").run()

    with open(benches_dir / "second" / "bench.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["bench"]["http_port"] == 8001
    assert data["bench"]["socketio_port"] == 9001
    assert data["redis"]["cache_port"] == 13001
    assert data["redis"]["queue_port"] == 11001
    assert data["admin"]["port"] == 7001


def test_new_command_inherits_sibling_jwks_url_and_audience(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The remote JWKS issuer is server-wide (common_config.toml), so a new
    bench carries both the URL and the audience forward from a sibling that
    already trusts one."""
    from pilot.commands.bench.create import NewCommand
    from pilot.config import BenchConfig
    from pilot.core.bench.creator import BenchCreator

    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(BenchCreator, "_port_is_live", staticmethod(lambda port: False))
    benches_dir = tmp_path / "benches"
    NewCommand(target_directory=benches_dir / "first", bench_name="first").run()
    with BenchConfig.open(benches_dir / "first") as config:
        config.admin.jwks_url = "https://issuer.example.com/jwks.json"
        config.admin.jwks_audience = "bench-fleet"

    NewCommand(target_directory=benches_dir / "second", bench_name="second").run()
    inherited = BenchConfig.read(benches_dir / "second").admin
    assert inherited.jwks_url == "https://issuer.example.com/jwks.json"
    assert inherited.jwks_audience == "bench-fleet"


def test_new_command_first_bench_has_no_jwks_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pilot.commands.bench.create import NewCommand
    from pilot.core.bench.creator import BenchCreator

    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(BenchCreator, "_port_is_live", staticmethod(lambda port: False))
    target = tmp_path / "benches" / "only"
    NewCommand(target_directory=target, bench_name="only").run()
    assert BenchConfig.read(target).admin.jwks_url == ""


def test_new_command_postgres_bench_has_no_password_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`pilot new` alone must not provision the shared DB server - only `pilot
    init` does (BenchInitializer._ensure_database_credentials)."""
    from pilot.commands.bench.create import NewCommand

    monkeypatch.setattr("builtins.input", lambda _: "")
    benches_dir = tmp_path / "benches"
    NewCommand(target_directory=benches_dir / "pg", bench_name="pg", database="postgres").run()

    with open(benches_dir / "pg" / "bench.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["bench"]["db_type"] == "postgres"
    assert not CommonConfig.read(benches_dir).postgres.root_password


def test_new_command_second_postgres_bench_inherits_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second Postgres bench reuses the shared server password."""
    from pilot.commands.bench.create import NewCommand

    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr("pilot.utils._port_is_live", lambda port: False)
    benches_dir = tmp_path / "benches"
    NewCommand(target_directory=benches_dir / "pg1", bench_name="pg1", database="postgres").run()
    _ensure_database_credentials(benches_dir / "pg1")
    password = CommonConfig.read(benches_dir).postgres.root_password
    assert password
    NewCommand(target_directory=benches_dir / "pg2", bench_name="pg2", database="postgres").run()
    _ensure_database_credentials(benches_dir / "pg2")

    assert CommonConfig.read(benches_dir).postgres.root_password == password
    assert BenchConfig.read(benches_dir / "pg2").postgres.root_password == password


def test_new_command_postgres_port_is_not_offset_between_benches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Postgres port stays shared while bench-local ports are offset."""
    from pilot.commands.bench.create import NewCommand
    from pilot.core.bench.creator import BenchCreator

    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(BenchCreator, "_port_is_live", staticmethod(lambda port: False))
    monkeypatch.setattr("pilot.utils._port_is_live", lambda port: False)
    benches_dir = tmp_path / "benches"
    NewCommand(target_directory=benches_dir / "first", bench_name="first", database="postgres").run()
    _ensure_database_credentials(benches_dir / "first")
    NewCommand(target_directory=benches_dir / "second", bench_name="second", database="postgres").run()

    with open(benches_dir / "second" / "bench.toml", "rb") as f:
        data = tomllib.load(f)
    assert CommonConfig.read(benches_dir).postgres.port == PostgresConfig().port
    assert data["bench"]["http_port"] == 8001  # other ports still offset


def test_new_command_postgres_port_ignores_live_scan_on_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS Postgres uses Homebrew's default service port."""
    from pilot.commands.bench.create import NewCommand

    monkeypatch.setattr("builtins.input", lambda _: "")
    default_port = PostgresConfig().port
    # The default port reads as live, which would normally push the picker up.
    monkeypatch.setattr("pilot.utils._port_is_live", lambda port: port == default_port)
    target = tmp_path / "benches" / "pg"
    with patch("pilot.managers.platform.is_macos", return_value=True):
        NewCommand(target_directory=target, bench_name="pg", database="postgres").run()
        _ensure_database_credentials(target)

    assert CommonConfig.read(tmp_path / "benches").postgres.port == default_port


def test_new_command_mariadb_bench_has_no_password_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pilot.commands.bench.create import NewCommand

    monkeypatch.setattr("builtins.input", lambda _: "")
    NewCommand(target_directory=tmp_path / "benches" / "m", bench_name="m").run()

    with open(tmp_path / "benches" / "m" / "bench.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["bench"]["db_type"] == "mariadb"
    assert not CommonConfig.read(tmp_path / "benches").mariadb.root_password


def test_new_command_mariadb_port_is_not_offset_between_benches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MariaDB port stays shared while bench-local ports are offset."""
    from pilot.commands.bench.create import NewCommand
    from pilot.core.bench.creator import BenchCreator

    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(BenchCreator, "_port_is_live", staticmethod(lambda port: False))
    monkeypatch.setattr("pilot.utils._port_is_live", lambda port: False)
    benches_dir = tmp_path / "benches"
    NewCommand(target_directory=benches_dir / "first", bench_name="first").run()
    _ensure_database_credentials(benches_dir / "first")
    NewCommand(target_directory=benches_dir / "second", bench_name="second").run()

    with open(benches_dir / "second" / "bench.toml", "rb") as f:
        data = tomllib.load(f)
    assert CommonConfig.read(benches_dir).mariadb.port == MariaDBConfig().port
    assert data["bench"]["http_port"] == 8001  # other ports still offset


def test_new_command_mariadb_port_ignores_live_scan_on_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS MariaDB uses Homebrew's default service port."""
    from pilot.commands.bench.create import NewCommand

    monkeypatch.setattr("builtins.input", lambda _: "")
    default_port = MariaDBConfig().port
    # The default port reads as live, which would normally push the picker up.
    monkeypatch.setattr("pilot.utils._port_is_live", lambda port: port == default_port)
    target = tmp_path / "benches" / "m"
    with patch("pilot.managers.platform.is_macos", return_value=True):
        NewCommand(target_directory=target, bench_name="m").run()
        _ensure_database_credentials(target)

    assert CommonConfig.read(tmp_path / "benches").mariadb.port == default_port


def test_new_command_second_mariadb_bench_inherits_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second MariaDB bench reuses the shared server password."""
    from pilot.commands.bench.create import NewCommand

    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr("pilot.utils._port_is_live", lambda port: False)
    benches_dir = tmp_path / "benches"
    NewCommand(target_directory=benches_dir / "m1", bench_name="m1").run()
    _ensure_database_credentials(benches_dir / "m1")
    password = CommonConfig.read(benches_dir).mariadb.root_password
    # Random, not the old guessable hardcoded default.
    assert password != "root"
    assert len(password) == 16  # secrets.token_hex(nbytes=8)

    NewCommand(target_directory=benches_dir / "m2", bench_name="m2").run()
    _ensure_database_credentials(benches_dir / "m2")
    assert CommonConfig.read(benches_dir).mariadb.root_password == password
    assert BenchConfig.read(benches_dir / "m2").mariadb.root_password == password


def test_new_command_skips_offset_with_live_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An orphaned process holding a port with no matching bench.toml must
    also be avoided, not just offsets already on disk."""
    from pilot.commands.bench.create import NewCommand
    from pilot.core.bench.creator import BenchCreator

    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(BenchCreator, "_port_is_live", staticmethod(lambda port: port == 8000))

    target = tmp_path / "benches" / "my-bench"
    NewCommand(target_directory=target, bench_name="my-bench").run()

    with open(target / "bench.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["bench"]["http_port"] == 8001


def test_new_command_skips_offset_with_live_admin_internal_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Port offset avoids the derived admin internal port too."""
    from pilot.commands.bench.create import NewCommand
    from pilot.core.bench.creator import BenchCreator

    monkeypatch.setattr("builtins.input", lambda _: "")
    # 7001 is admin.port(7000) + 1 at offset 0 - without the internal-port
    # check, offset 0 would be wrongly accepted since nothing else probes it.
    # (It also collides with the plain admin.port base check one offset later,
    # at offset 1, which is why the picker lands on offset 2, not 1.)
    monkeypatch.setattr(BenchCreator, "_port_is_live", staticmethod(lambda port: port == 7001))

    target = tmp_path / "benches" / "my-bench"
    NewCommand(target_directory=target, bench_name="my-bench").run()

    with open(target / "bench.toml", "rb") as f:
        data = tomllib.load(f)
    # The concrete regression guard: offset 0 (http_port 8000) must not be
    # chosen, since its admin.internal_port (7001) is already live.
    assert data["bench"]["http_port"] == 8002


def test_new_site_raises_if_site_exists(tmp_path: Path) -> None:
    from pilot.core.site import _validate_new_site

    bench = make_bench(tmp_path)
    bench.create_directories()
    site_dir = bench.sites_path / "site1.localhost"
    site_dir.mkdir()
    (site_dir / "site_config.json").write_text("{}")

    with pytest.raises(BenchError, match="already exists"):
        _validate_new_site(bench, "site1.localhost", ["frappe"])


def test_new_site_raises_if_app_not_in_apps_txt(tmp_path: Path) -> None:
    from pilot.core.site import _validate_new_site

    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.sites_path / "apps.txt").write_text("frappe\n")

    with pytest.raises(BenchError, match="erpnext"):
        _validate_new_site(bench, "site1.localhost", ["erpnext"])


def test_new_site_validate_passes_when_all_ok(tmp_path: Path) -> None:
    from pilot.core.site import _validate_new_site

    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.sites_path / "apps.txt").write_text("frappe\n")

    _validate_new_site(bench, "site1.localhost", ["frappe"])  # no raise


def test_new_site_validate_passes_with_no_apps_requested(tmp_path: Path) -> None:
    from pilot.core.site import _validate_new_site

    bench = make_bench(tmp_path)
    bench.create_directories()

    _validate_new_site(bench, "site1.localhost", [])  # no raise


def test_build_missing_assets_skips_cloned_but_unregistered_apps(tmp_path: Path) -> None:
    from pilot.config import SiteConfig
    from pilot.core.site import Site

    bench = make_bench(tmp_path)
    bench.create_directories()
    for name in ("frappe", "builder"):
        (bench.apps_path / name / ".git").mkdir(parents=True)
    # builder is cloned on disk but never registered - it isn't installed.
    (bench.sites_path / "apps.txt").write_text("frappe\n")

    with patch("pilot.managers.environment.PythonEnvManager.build_assets_for_app") as build:
        Site(SiteConfig(name="site1.localhost", apps=["frappe"]), bench)._build_missing_assets()

    built = {call.args[0].config.name for call in build.call_args_list}
    assert built == {"frappe"}


def test_remove_app_raises_when_app_directory_missing(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()

    with pytest.raises(BenchError, match="not found"):
        bench.app("nonexistent").ensure_removable()


def test_remove_app_raises_when_removing_framework_app(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "frappe").mkdir()

    with pytest.raises(BenchError, match="framework"):
        bench.app("frappe").ensure_removable()


def test_remove_app_confirm_raises_on_negative_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pilot.commands.apps.remove import RemoveAppCommand

    bench = make_bench(tmp_path)
    (bench.apps_path / "myapp").mkdir(parents=True)
    monkeypatch.setattr("builtins.input", lambda _: "n")

    with pytest.raises(BenchError, match="Aborted"):
        RemoveAppCommand(bench, app_name="myapp").confirm("Remove?")


def test_remove_app_confirm_passes_on_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pilot.commands.apps.remove import RemoveAppCommand

    bench = make_bench(tmp_path)
    (bench.apps_path / "myapp").mkdir(parents=True)
    monkeypatch.setattr("builtins.input", lambda _: "y")
    RemoveAppCommand(bench, app_name="myapp").confirm("Remove?")  # no raise


def test_remove_app_confirm_skipped_when_skip_confirm(tmp_path: Path) -> None:
    from pilot.commands.apps.remove import RemoveAppCommand

    bench = make_bench(tmp_path)
    (bench.apps_path / "myapp").mkdir(parents=True)
    RemoveAppCommand(bench, app_name="myapp", skip_confirm=True).confirm(
        "Remove?", skip=True
    )  # no raise, no input


def test_remove_app_removes_app_from_apps_txt(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "myapp").mkdir()
    apps_txt = bench.sites_path / "apps.txt"
    apps_txt.write_text("frappe\nmyapp\nerpnext\n")

    bench.app("myapp")._deregister()

    lines = [line for line in apps_txt.read_text().splitlines() if line.strip()]
    assert "myapp" not in lines
    assert "frappe" in lines
    assert "erpnext" in lines


def test_remove_app_removes_from_apps_txt_missing_file(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "myapp").mkdir()
    # apps.txt does not exist - should not raise

    bench.app("myapp")._deregister()


def test_remove_app_full_flow_no_sites(tmp_path: Path) -> None:
    from pilot.commands.apps.remove import RemoveAppCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "erpnext"
    app_dir.mkdir()
    (bench.sites_path / "apps.txt").write_text("frappe\nerpnext\n")

    cmd = RemoveAppCommand(bench, app_name="erpnext", skip_confirm=True)
    with patch("pilot.managers.environment.PythonEnvManager.uninstall_app"):
        cmd.run()

    assert not app_dir.exists()
    remaining = [line for line in (bench.sites_path / "apps.txt").read_text().splitlines() if line.strip()]
    assert "erpnext" not in remaining


def test_uninstall_app_raises_if_site_not_found(tmp_path: Path) -> None:
    from pilot.commands.apps.uninstall import UninstallAppCommand

    bench = make_bench(tmp_path)
    bench.create_directories()

    with pytest.raises(BenchError, match="does not exist"):
        UninstallAppCommand(bench, site_name="site1.localhost", app_names=["myapp"]).run()


def test_uninstall_app_raises_if_app_not_installed(tmp_path: Path) -> None:
    from pilot.commands.apps.uninstall import UninstallAppCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "myapp").mkdir()
    site_dir = bench.sites_path / "site1.localhost"
    site_dir.mkdir()
    (site_dir / "site_config.json").write_text("{}")

    cmd = UninstallAppCommand(bench, site_name="site1.localhost", app_names=["myapp"])
    with (
        patch("pilot.core.site.Site.list_apps", return_value=["frappe"]),
        pytest.raises(BenchError, match="not installed"),
    ):
        cmd.run()


def test_uninstall_app_calls_site_uninstall_when_installed(tmp_path: Path) -> None:
    from pilot.commands.apps.uninstall import UninstallAppCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "myapp").mkdir()
    site_dir = bench.sites_path / "site1.localhost"
    site_dir.mkdir()
    (site_dir / "site_config.json").write_text("{}")

    cmd = UninstallAppCommand(bench, site_name="site1.localhost", app_names=["myapp"])
    with (
        patch("pilot.core.site.Site.list_apps", return_value=["frappe", "myapp"]),
        patch("pilot.core.site.Site.uninstall_app") as mock_uninstall,
    ):
        cmd.run()
        mock_uninstall.assert_called_once()


def test_frappe_command_raises_if_venv_python_missing(tmp_path: Path) -> None:
    from pilot.commands.runtime.frappe import FrappeCommand

    bench = make_bench(tmp_path)

    with pytest.raises(BenchError, match="not found"):
        FrappeCommand(bench, args=("migrate",)).run()


def test_frappe_command_calls_subprocess_with_frappe_call(tmp_path: Path) -> None:
    from pilot.commands.runtime.frappe import FrappeCommand

    bench = make_bench(tmp_path)
    (tmp_path / "env" / "bin").mkdir(parents=True)
    (tmp_path / "env" / "bin" / "python").touch()

    mock_result = MagicMock(returncode=0)
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        with pytest.raises(SystemExit) as exc_info:
            FrappeCommand(bench, args=("migrate",)).run()
        assert exc_info.value.code == 0
        called_args = mock_run.call_args[0][0]
        assert "frappe.utils.bench_helper" in " ".join(called_args)
        assert "frappe" in called_args
        assert "migrate" in called_args


def test_frappe_command_exits_with_subprocess_returncode(tmp_path: Path) -> None:
    from pilot.commands.runtime.frappe import FrappeCommand

    bench = make_bench(tmp_path)
    (tmp_path / "env" / "bin").mkdir(parents=True)
    (tmp_path / "env" / "bin" / "python").touch()

    with patch("subprocess.run", return_value=MagicMock(returncode=42)):
        with pytest.raises(SystemExit) as exc_info:
            FrappeCommand(bench, args=("foo",)).run()
        assert exc_info.value.code == 42


def test_build_command_force_calls_frappe_build(tmp_path: Path) -> None:
    from pilot.commands.runtime.build import BuildCommand

    bench = make_bench(tmp_path)
    bench.create_directories()

    with patch("pilot.managers.environment.PythonEnvManager.build_assets") as mock_build:
        BuildCommand(bench, force=True).run()
        mock_build.assert_called_once()


def test_build_command_default_uses_prebuilt_per_app(tmp_path: Path) -> None:
    from pilot.commands.runtime.build import BuildCommand

    bench = make_bench(tmp_path)
    bench.create_directories()

    with (
        patch("pilot.managers.environment.PythonEnvManager.build_assets_for_app") as mock_build,
        patch.object(bench, "apps", return_value=[]),
    ):
        BuildCommand(bench).run()
        mock_build.assert_not_called()


def fake_app(name: str) -> MagicMock:
    app = MagicMock()
    app.config.name = name
    return app


def test_build_command_builds_only_named_apps(tmp_path: Path) -> None:
    from pilot.commands.runtime.build import BuildCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    hrms = fake_app("hrms")

    with (
        patch("pilot.managers.environment.PythonEnvManager.build_assets_for_app") as mock_build,
        patch.object(bench, "apps", return_value=[fake_app("frappe"), hrms]),
    ):
        BuildCommand(bench, apps=["hrms"]).run()
        mock_build.assert_called_once_with(hrms, force=False)


def test_build_command_named_apps_with_force_skips_prebuilt(tmp_path: Path) -> None:
    from pilot.commands.runtime.build import BuildCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    hrms = fake_app("hrms")

    with (
        patch("pilot.managers.environment.PythonEnvManager.build_assets") as mock_build_all,
        patch("pilot.managers.environment.PythonEnvManager.build_assets_for_app") as mock_build,
        patch.object(bench, "apps", return_value=[fake_app("frappe"), hrms]),
    ):
        BuildCommand(bench, apps=["hrms"], force=True).run()
        mock_build.assert_called_once_with(hrms, force=True)
        mock_build_all.assert_not_called()


def test_build_command_rejects_unknown_app(tmp_path: Path) -> None:
    from pilot.commands.runtime.build import BuildCommand

    bench = make_bench(tmp_path)
    bench.create_directories()

    with (
        patch("pilot.managers.environment.PythonEnvManager.build_assets_for_app") as mock_build,
        patch.object(bench, "apps", return_value=[fake_app("frappe")]),
        pytest.raises(BenchError, match="nope"),
    ):
        BuildCommand(bench, apps=["nope"]).run()
        mock_build.assert_not_called()


def test_requirements_skips_app_without_python_setup_files(tmp_path: Path) -> None:
    from pilot.core.bench.runtime import BenchRuntime

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "bare-app"
    app_dir.mkdir()
    (app_dir / ".git").mkdir()
    # No pyproject.toml or setup.py

    with (
        patch("pilot.managers.environment.ensure_uv", return_value="uv"),
        patch("pilot.utils.run_command") as mock_rc,
    ):
        BenchRuntime(bench)._install_python_requirements(lambda _message: None)
        mock_rc.assert_not_called()


def test_requirements_installs_app_with_pyproject_toml(tmp_path: Path) -> None:
    from pilot.core.bench.runtime import BenchRuntime

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "myapp"
    app_dir.mkdir()
    (app_dir / ".git").mkdir()
    (app_dir / "pyproject.toml").write_text("[project]\nname = 'myapp'\n")

    with (
        patch("pilot.managers.environment.ensure_uv", return_value="uv"),
        patch("pilot.utils.run_command") as mock_rc,
    ):
        BenchRuntime(bench)._install_python_requirements(lambda _message: None)
        mock_rc.assert_called_once()


def test_requirements_installs_app_with_setup_py(tmp_path: Path) -> None:
    from pilot.core.bench.runtime import BenchRuntime

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "myapp"
    app_dir.mkdir()
    (app_dir / ".git").mkdir()
    (app_dir / "setup.py").write_text("from setuptools import setup; setup()\n")

    with (
        patch("pilot.managers.environment.ensure_uv", return_value="uv"),
        patch("pilot.utils.run_command") as mock_rc,
    ):
        BenchRuntime(bench)._install_python_requirements(lambda _message: None)
        mock_rc.assert_called_once()


def test_requirements_skips_js_for_app_without_package_json(tmp_path: Path) -> None:
    from pilot.core.bench.runtime import BenchRuntime

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "myapp"
    app_dir.mkdir()
    (app_dir / ".git").mkdir()
    # No package.json

    with patch("pilot.utils.run_command") as mock_rc:
        BenchRuntime(bench)._install_js_requirements(lambda _message: None)
        mock_rc.assert_not_called()


def test_requirements_installs_js_for_app_with_package_json(tmp_path: Path) -> None:
    from pilot.core.bench.runtime import BenchRuntime

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "myapp"
    app_dir.mkdir()
    (app_dir / ".git").mkdir()
    (app_dir / "package.json").write_text('{"name": "myapp"}\n')

    with (
        patch("pilot.utils.get_yarn_bin", return_value="yarn"),
        patch("pilot.utils.run_command") as mock_rc,
    ):
        BenchRuntime(bench)._install_js_requirements(lambda _message: None)
        mock_rc.assert_called_once()
        assert mock_rc.call_args[0][0] == ["yarn", "install"]


def test_upgrade_command_performs_upgrade() -> None:
    from pilot.commands.admin.upgrade import UpgradeCommand

    with patch("pilot.updater.perform_upgrade") as mock_upgrade:
        UpgradeCommand().run()

    mock_upgrade.assert_called_once()


def test_bench_update_apps_raises_on_command_error(tmp_path: Path) -> None:
    from pilot.exceptions import CommandError, MigrateError

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "myapp"
    app_dir.mkdir()
    (app_dir / ".git").mkdir()

    with (
        patch("pilot.core.app.App.update", side_effect=CommandError("git error")),
        patch("pilot.integrations.marketplace.Marketplace.registry", return_value=[]),
        pytest.raises(MigrateError),
    ):
        bench._update_apps(None, lambda message: None)


def _commit_ahead_of_head(repo: Path) -> str:
    """Commit on top of the repo's HEAD, left unchecked-out - what a release
    advertises once the app has been running an older commit."""
    import subprocess

    from pilot.internal.git import GitRepo

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("config", "user.email", "t@t.com")
    git("config", "user.name", "t")
    git("commit", "-q", "--allow-empty", "-m", "installed")
    head = GitRepo(repo).head_sha
    git("commit", "-q", "--allow-empty", "-m", "published")
    published = GitRepo(repo).head_sha
    git("reset", "--hard", "-q", head)
    return published


def test_bench_update_apps_passes_marketplace_pin_to_app_update(tmp_path: Path) -> None:
    import subprocess

    from pilot.core.app import RevisionPin
    from tests.pilot.marketplace_registry import publish

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "helpdesk"
    app_dir.mkdir()
    _write_installable_app(app_dir, "helpdesk")
    subprocess.run(["git", "init", "-q", "-b", "main", str(app_dir)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(app_dir),
            "remote",
            "add",
            "origin",
            "https://github.com/frappe/helpdesk",
        ],
        check=True,
    )

    published = _commit_ahead_of_head(app_dir)
    registry = [
        {
            "name": "helpdesk",
            "repo": "https://github.com/frappe/helpdesk",
            "releases": [{"version": "1.1.0", "branch": "main", "commit": published}],
        }
    ]

    publish(registry)
    with patch("pilot.core.app.App.update") as mock_update:
        bench._update_apps(None, lambda message: None)

    mock_update.assert_called_once_with(pin=RevisionPin(kind="commit", ref=published))


def test_bench_update_apps_skips_a_marketplace_app_with_nothing_newer(tmp_path: Path) -> None:
    """Pulling the branch for a registry app would install unpublished code."""
    import subprocess

    from tests.pilot.marketplace_registry import publish

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "helpdesk"
    app_dir.mkdir()
    _write_installable_app(app_dir, "helpdesk")
    subprocess.run(["git", "init", "-q", "-b", "main", str(app_dir)], check=True)
    subprocess.run(
        ["git", "-C", str(app_dir), "remote", "add", "origin", "https://github.com/frappe/helpdesk"],
        check=True,
    )

    from pilot.internal.git import GitRepo

    _commit_ahead_of_head(app_dir)  # leaves HEAD on a real commit
    registry = [
        {
            "name": "helpdesk",
            "repo": "https://github.com/frappe/helpdesk",
            # Exactly what is checked out - no forward release.
            "releases": [{"version": "1.0.0", "branch": "main", "commit": GitRepo(app_dir).head_sha}],
        }
    ]

    publish(registry)
    with patch("pilot.core.app.App.update") as mock_update:
        bench._update_apps(None, lambda message: None)

    mock_update.assert_not_called()


def test_bench_update_apps_updates_an_app_outside_the_registry_branch_wide(tmp_path: Path) -> None:
    import subprocess

    from tests.pilot.marketplace_registry import publish

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "private_app"
    app_dir.mkdir()
    _write_installable_app(app_dir, "private_app")
    subprocess.run(["git", "init", "-q", "-b", "main", str(app_dir)], check=True)

    publish([])
    with patch("pilot.core.app.App.update") as mock_update:
        bench._update_apps(None, lambda message: None)

    mock_update.assert_called_once_with(pin=None)


def test_bench_update_apps_uses_captured_target_for_unpinned_app(tmp_path: Path) -> None:
    import subprocess

    from pilot.core.app import RevisionPin
    from tests.pilot.marketplace_registry import publish

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "helpdesk"
    app_dir.mkdir()
    _write_installable_app(app_dir, "helpdesk")
    subprocess.run(["git", "init", "-q", str(app_dir)], check=True)

    publish([])
    with patch("pilot.core.app.App.update") as mock_update:
        # A captured pin is used exactly as given - never re-resolved live.
        bench._update_apps(None, lambda message: None, {"helpdesk": RevisionPin(kind="commit", ref="deadbeef")})

    mock_update.assert_called_once_with(pin=RevisionPin(kind="commit", ref="deadbeef"))


def test_drop_site_removes_site_from_bench_toml(tmp_path: Path) -> None:
    import tomllib

    from pilot.config import SiteConfig
    from pilot.core.site import Site

    bench = make_bench(tmp_path)
    bench_toml = tmp_path / "bench.toml"
    bench_toml.write_text(
        '[bench]\nname = "test-bench"\npython = "3.14"\n\n'
        '[[apps]]\nname = "frappe"\nrepo = "..."\nbranch = "version-16"\n\n'
        '[[sites]]\nname = "site1.localhost"\n\n'
        '[[sites]]\nname = "site2.localhost"\n\n'
        '[mariadb]\nhost = "localhost"\nport = 3306\nroot_password = "root"\n\n'
        "[redis]\nport = 13000\n\n"
        '[[workers]]\nqueues = ["default", "short", "long"]\ncount = 1\n'
    )

    site = Site(SiteConfig(name="site1.localhost", apps=[]), bench)
    site._remove_from_bench_toml()

    with bench_toml.open("rb") as fh:
        raw = tomllib.load(fh)
    names = [s.get("name") for s in raw.get("sites", [])]
    assert "site1.localhost" not in names
    assert "site2.localhost" in names


def test_drop_site_removes_from_toml_when_no_sites_key(tmp_path: Path) -> None:
    from pilot.config import SiteConfig
    from pilot.core.site import Site

    bench = make_bench(tmp_path)
    bench_toml = tmp_path / "bench.toml"
    bench_toml.write_text(
        '[bench]\nname = "test-bench"\npython = "3.14"\n\n'
        '[[apps]]\nname = "frappe"\nrepo = "..."\nbranch = "version-16"\n\n'
        '[mariadb]\nhost = "localhost"\nport = 3306\nroot_password = "root"\n\n'
        "[redis]\nport = 13000\n\n"
        '[[workers]]\nqueues = ["default", "short", "long"]\ncount = 1\n'
    )

    site = Site(SiteConfig(name="nonexistent", apps=[]), bench)
    site._remove_from_bench_toml()  # no raise


def test_restart_dev_bench_prints_guidance(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from pilot.commands.runtime.restart import RestartCommand

    bench = make_bench(tmp_path)  # production disabled by default
    RestartCommand(bench).run()
    out = capsys.readouterr().out
    assert "only for production benches" in out


def test_restart_production_incomplete_prints_repair(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from pilot.commands.runtime.restart import RestartCommand

    bench = make_bench(tmp_path)
    bench.config.production.enabled = True
    bench.config.production.process_manager = "systemd"
    with patch("pilot.managers.processes.local.ProcessManager.for_bench") as create:
        mgr = MagicMock()
        mgr.is_configured.return_value = False
        create.return_value = mgr
        RestartCommand(bench).run()
    out = capsys.readouterr().out
    assert "deployment is incomplete" in out
    mgr.restart.assert_not_called()


def test_restart_production_restarts_when_configured(tmp_path: Path) -> None:
    from pilot.commands.runtime.restart import RestartCommand

    bench = make_bench(tmp_path)
    bench.config.production.enabled = True
    bench.config.production.process_manager = "supervisor"
    with patch("pilot.managers.processes.local.ProcessManager.for_bench") as create:
        mgr = MagicMock()
        mgr.is_configured.return_value = True
        create.return_value = mgr
        RestartCommand(bench).run()
    mgr.write_config.assert_called_once()
    mgr.restart.assert_called_once()


def test_ls_lists_benches_with_mode_and_address(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from pilot.commands.bench.list import ListCommand

    benches = tmp_path / "benches"
    (benches / "alpha").mkdir(parents=True)
    (benches / "alpha" / "bench.toml").write_text(
        '[bench]\nname = "alpha"\n\n[production]\nenabled = true\nprocess_manager = "systemd"\n\n'
        '[admin]\ndomain = "alpha-admin.example.com"\n'
    )
    (benches / "beta").mkdir(parents=True)
    (benches / "beta" / "bench.toml").write_text('[bench]\nname = "beta"\n\n[admin]\nport = 7005\n')

    with (
        patch("pilot.utils.cli_root", return_value=tmp_path),
        patch("pilot.commands.bench.list.ListCommand._state", return_value="stopped"),
    ):
        ListCommand().run()

    out = capsys.readouterr().out
    assert "alpha" in out and "production" in out and "alpha-admin.example.com" in out
    assert "beta" in out and "development" in out and "http://localhost:7005" in out


def test_ls_shows_manager_and_domain_before_wizard_finishes(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from pilot.commands.bench.list import ListCommand

    benches = tmp_path / "benches"
    (benches / "pending").mkdir(parents=True)
    (benches / "pending" / "bench.toml").write_text(
        '[bench]\nname = "pending"\n\n[production]\nenabled = false\nprocess_manager = "systemd"\n\n'
        '[admin]\ndomain = "pending-admin.example.com"\n'
    )

    with (
        patch("pilot.utils.cli_root", return_value=tmp_path),
        patch("pilot.commands.bench.list.ListCommand._state", return_value="stopped"),
        patch(
            "pilot.managers.nginx.NginxManager.has_admin_cert",
            new_callable=PropertyMock,
            return_value=False,
        ),
    ):
        ListCommand().run()

    out = capsys.readouterr().out
    assert "development" in out
    assert "systemd" in out
    assert "http://pending-admin.example.com" in out
    assert "foreground" not in out


def test_ls_state_admin_active_when_workload_down_but_admin_up(tmp_path: Path) -> None:
    from pilot.commands.bench.list import ListCommand
    from pilot.managers.processes.local import ProcessManager

    bench = make_bench(tmp_path)
    with patch.object(ProcessManager, "for_bench") as create:
        manager = create.return_value
        manager.is_running.return_value = False
        manager.is_admin_running.return_value = True
        assert ListCommand()._state(bench, production=True) == "admin"
        manager.is_admin_running.return_value = False
        assert ListCommand()._state(bench, production=True) == "stopped"
        manager.is_running.return_value = True
        assert ListCommand()._state(bench, production=True) == "running"


def test_ls_empty_when_no_benches(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from pilot.commands.bench.list import ListCommand

    (tmp_path / "benches").mkdir()
    with patch("pilot.utils.cli_root", return_value=tmp_path):
        ListCommand().run()
    assert "No benches yet" in capsys.readouterr().out


def _mark_initialized(bench: Bench) -> None:
    (bench.path / "env" / "bin").mkdir(parents=True, exist_ok=True)
    (bench.path / "env" / "bin" / "python").write_text("")


def test_start_dev_uninitialized_runs_wizard(tmp_path: Path) -> None:
    from pilot.core.bench.runtime import BenchRuntime

    bench = make_bench(tmp_path)  # no process manager → dev
    with (
        patch.object(BenchRuntime, "_start_wizard") as wizard,
        patch.object(BenchRuntime, "_rebuild_manager_config") as rebuild,
        patch("pilot.managers.processes.local.ProcessManager.stop"),
    ):
        BenchRuntime(bench).start(lambda _message: None)
    wizard.assert_called_once()
    rebuild.assert_not_called()


def test_start_dev_initialized_stops_then_starts(tmp_path: Path) -> None:
    from pilot.core.bench.runtime import BenchRuntime

    bench = make_bench(tmp_path)  # dev
    _mark_initialized(bench)
    with (
        patch("pilot.managers.processes.local.ProcessManager.stop") as stop,
        patch.object(BenchRuntime, "_rebuild_manager_config") as rebuild,
        patch.object(BenchRuntime, "_ensure_admin_dist"),
        patch("pilot.managers.processes.local.ProcessManager.start") as start,
    ):
        BenchRuntime(bench).start(lambda _message: None)
    stop.assert_called_once()
    rebuild.assert_called_once()
    start.assert_called_once()


def test_start_dev_propagates_stop_failure(tmp_path: Path) -> None:
    from pilot.core.bench.runtime import BenchRuntime

    bench = make_bench(tmp_path)
    _mark_initialized(bench)
    with (
        patch(
            "pilot.managers.processes.local.ProcessManager.stop",
            side_effect=BenchError("Timed out waiting for bench ports."),
        ),
        patch("pilot.managers.processes.local.ProcessManager.start") as start,
        pytest.raises(BenchError, match="Timed out waiting for bench ports"),
    ):
        BenchRuntime(bench).start(lambda _message: None)

    start.assert_not_called()


def test_start_dev_swallows_not_running_stop(tmp_path: Path) -> None:
    from pilot.core.bench.runtime import BenchRuntime
    from pilot.exceptions import BenchNotRunningError

    bench = make_bench(tmp_path)
    _mark_initialized(bench)
    with (
        patch(
            "pilot.managers.processes.local.ProcessManager.stop",
            side_effect=BenchNotRunningError("Bench is not running."),
        ),
        patch.object(BenchRuntime, "_rebuild_manager_config"),
        patch.object(BenchRuntime, "_ensure_admin_dist"),
        patch("pilot.managers.processes.local.ProcessManager.start") as start,
    ):
        BenchRuntime(bench).start(lambda _message: None)

    start.assert_called_once()


def test_start_dev_watch_admin_js_from_config_skips_static_admin_build(tmp_path: Path) -> None:
    from pilot.core.bench.runtime import BenchRuntime

    bench = make_bench(tmp_path)
    bench.config.watch_admin_js = True
    _mark_initialized(bench)
    with (
        patch("pilot.managers.processes.local.ProcessManager.stop"),
        patch.object(BenchRuntime, "_rebuild_manager_config"),
        patch.object(BenchRuntime, "_ensure_admin_dist") as ensure_admin_dist,
        patch("pilot.managers.processes.local.ProcessManager.start"),
    ):
        BenchRuntime(bench).start(lambda _message: None)

    ensure_admin_dist.assert_not_called()


def test_start_production_uninitialized_brings_up_admin(tmp_path: Path) -> None:
    # A systemd bench that isn't initialized yet runs its admin under systemd
    # (to serve the wizard), not a foreground wizard server.
    from pilot.core.bench.runtime import BenchRuntime

    bench = make_bench(tmp_path)
    bench.config.production.process_manager = "systemd"
    bench.config.admin.domain = "admin.example.com"
    with (
        patch("pilot.managers.processes.systemd.SystemdProcessManager.start_admin") as start_admin,
        patch.object(BenchRuntime, "_rebuild_manager_config") as rebuild,
        patch.object(BenchRuntime, "_start_wizard") as wizard,
    ):
        BenchRuntime(bench).start(lambda _message: None)
    start_admin.assert_called_once()
    rebuild.assert_not_called()
    wizard.assert_not_called()


def test_start_production_initialized_starts_manager(tmp_path: Path) -> None:
    from pilot.core.bench.runtime import BenchRuntime

    bench = make_bench(tmp_path)
    bench.config.production.process_manager = "systemd"
    _mark_initialized(bench)
    with (
        patch(
            "pilot.managers.processes.systemd.SystemdProcessManager.is_configured",
            return_value=True,
        ),
        patch.object(BenchRuntime, "_rebuild_manager_config") as rebuild,
        patch("pilot.managers.processes.systemd.SystemdProcessManager.start") as start,
    ):
        BenchRuntime(bench).start(lambda _message: None)
    rebuild.assert_called_once()
    start.assert_called_once()


def test_start_rebuild_config_writes_process_and_common_site_config(tmp_path: Path) -> None:
    from pilot.core.bench.runtime import BenchRuntime

    bench = make_bench(tmp_path)
    manager = MagicMock()
    with patch.object(bench, "write_common_site_config") as common_site:
        BenchRuntime(bench)._rebuild_manager_config(manager)

    manager.write_config.assert_called_once()
    common_site.assert_called_once()


def test_write_common_site_config_preserves_custom_keys(tmp_path: Path) -> None:
    import json

    bench = make_bench(tmp_path)
    bench.sites_path.mkdir(parents=True)
    config_path = bench.sites_path / "common_site_config.json"
    config_path.write_text('{"server_script_enabled": 1, "redis_cache": "stale"}')

    bench.write_common_site_config()

    config = json.loads(config_path.read_text())
    assert config["server_script_enabled"] == 1
    assert config["redis_cache"] == "redis://localhost:13000"


def test_write_common_site_config_leaves_developer_mode_to_sites(tmp_path: Path) -> None:
    import json

    bench = make_bench(tmp_path)
    bench.sites_path.mkdir(parents=True)
    config_path = bench.sites_path / "common_site_config.json"
    bench.config.allow_developer_mode = True

    bench.write_common_site_config()

    assert "developer_mode" not in json.loads(config_path.read_text())


def _drop_config(name: str) -> BenchConfig:
    return BenchConfig(
        name=name,
        python_version="3.14",
        apps=[AppConfig(name="frappe", repo="x", branch="y")],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(groups=[WorkerGroup(queues=["default"], count=1)]),
    )


def test_unmount_legacy_bind_mount_noop_when_not_mounted(tmp_path: Path) -> None:
    """A bench that was never volume-backed has nothing mounted at its dir, so
    this must be a silent no-op - no sudo calls, no fstab rewrite."""
    from pilot.managers.platform import unmount_legacy_bind_mount

    target = tmp_path / "not-a-mountpoint"
    target.mkdir()
    with patch("subprocess.run") as run:
        unmount_legacy_bind_mount(target)
    run.assert_not_called()


def test_unmount_legacy_bind_mount_unmounts_and_cleans_fstab(tmp_path: Path) -> None:
    """A leftover ZFS-era bind mount must be unmounted and its fstab line
    dropped, without depending on any ZFS/volume code being present."""
    from pilot.managers.platform import unmount_legacy_bind_mount

    target = tmp_path / "old-bench"
    target.mkdir()
    fstab = tmp_path / "fstab"
    fstab.write_text(f"UUID=abc / ext4 defaults 0 1\n/bench-pool/old-bench {target} none bind,nofail 0 0\n")

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["sudo", "tee"]:
            fstab.write_bytes(kwargs["input"])
        return MagicMock(returncode=0)

    with (
        patch("subprocess.run", side_effect=fake_run),
        patch.object(Path, "is_mount", return_value=True),
    ):
        unmount_legacy_bind_mount(target, fstab_path=fstab)

    assert ["sudo", "umount", "-l", str(target)] in calls
    assert fstab.read_text() == "UUID=abc / ext4 defaults 0 1\n"


def test_drop_bench_refuses_when_sites_exist(tmp_path: Path) -> None:
    from pilot.commands.bench.delete import DropBenchCommand

    bench = Bench(_drop_config("one"), tmp_path)
    site = tmp_path / "sites" / "a.localhost"
    site.mkdir(parents=True)
    (site / "site_config.json").write_text("{}")

    with pytest.raises(BenchError, match="site"):
        DropBenchCommand(bench, skip_confirm=True).run()
    # The bench directory must survive a refused drop.
    assert tmp_path.exists()


def test_drop_bench_deletes_directory_with_no_sites(tmp_path: Path) -> None:
    """Clean drop with no sites removes the bench directory."""
    from pilot.commands.bench.delete import DropBenchCommand

    benches = tmp_path / "benches"
    bench_dir = benches / "one"
    bench_dir.mkdir(parents=True)
    bench = Bench(_drop_config("one"), bench_dir)

    DropBenchCommand(bench, skip_confirm=True).run()
    assert not bench_dir.exists()


def test_build_admin_rejects_old_node(monkeypatch: pytest.MonkeyPatch) -> None:
    from admin.backend.frontend import _check_node_version

    monkeypatch.setattr("pilot.utils.run_command", lambda *a, **k: MagicMock(stdout=b"v18.20.8\n"))
    with pytest.raises(BenchError, match=r"Node\.js"):
        _check_node_version()


def test_build_admin_accepts_supported_node(monkeypatch: pytest.MonkeyPatch) -> None:
    from admin.backend.frontend import _check_node_version

    monkeypatch.setattr("pilot.utils.run_command", lambda *a, **k: MagicMock(stdout=b"v20.11.0\n"))
    _check_node_version()  # no raise


def test_build_admin_errors_when_node_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from admin.backend.frontend import _check_node_version

    def _missing(*a, **k):
        raise FileNotFoundError("node")

    monkeypatch.setattr("pilot.utils.run_command", _missing)
    with pytest.raises(BenchError, match=r"Node\.js is required"):
        _check_node_version()


def test_build_admin_errors_when_node_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_command raises on a failing exit code rather than returning it."""
    from admin.backend.frontend import _check_node_version
    from pilot.exceptions import CommandError

    def _failing(*a, **k):
        raise CommandError("Command 'node' failed with exit code 1.", returncode=1)

    monkeypatch.setattr("pilot.utils.run_command", _failing)
    with pytest.raises(BenchError, match=r"Node\.js is required"):
        _check_node_version()


def test_build_admin_installs_when_node_modules_missing(tmp_path: Path) -> None:
    from admin.backend.frontend import _is_npm_install_stale

    (tmp_path / "package.json").write_text("{}")

    assert _is_npm_install_stale(tmp_path) is True


def test_build_admin_installs_when_manifest_is_newer_than_installed_deps(tmp_path: Path) -> None:
    import os

    from admin.backend.frontend import _is_npm_install_stale

    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    install_state = node_modules / ".package-lock.json"
    install_state.write_text("{}")
    package_json = tmp_path / "package.json"
    package_json.write_text("{}")
    package_lock = tmp_path / "package-lock.json"
    package_lock.write_text("{}")
    os.utime(install_state, (100, 100))
    os.utime(package_json, (200, 200))
    os.utime(package_lock, (100, 100))

    assert _is_npm_install_stale(tmp_path) is True


def test_build_admin_skips_install_when_installed_deps_are_current(tmp_path: Path) -> None:
    import os

    from admin.backend.frontend import _is_npm_install_stale

    package_json = tmp_path / "package.json"
    package_json.write_text("{}")
    package_lock = tmp_path / "package-lock.json"
    package_lock.write_text("{}")
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    install_state = node_modules / ".package-lock.json"
    install_state.write_text("{}")
    os.utime(package_json, (100, 100))
    os.utime(package_lock, (100, 100))
    os.utime(install_state, (200, 200))

    assert _is_npm_install_stale(tmp_path) is False


def _admin_source_checkout(tmp_path: Path, src_mtime: int, built_mtime: int) -> Path:
    """A source checkout layout with a built dist; mtimes set to compare staleness."""
    import os

    cli_root = tmp_path / "repo"
    frontend = cli_root / "admin" / "frontend" / "dashboard"
    (frontend / "src").mkdir(parents=True)
    package_json = frontend / "package.json"
    package_json.write_text("{}")
    dist = cli_root / "admin" / "backend" / "static" / "dashboard"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("built")
    src_file = frontend / "src" / "App.vue"
    src_file.write_text("x")
    # Every source file shares src_mtime so the build mtime alone decides staleness.
    for source in (package_json, src_file):
        os.utime(source, (src_mtime, src_mtime))
    os.utime(dist / "index.html", (built_mtime, built_mtime))
    return cli_root


def test_start_rebuilds_admin_in_a_dev_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Staleness is decided inside build_admin_frontend (see test_frontend.py); this only
    checks _ensure_admin_dist delegates to it whenever a dev checkout can build."""
    from pilot.core.bench.runtime import BenchRuntime

    cli_root = _admin_source_checkout(tmp_path, src_mtime=100, built_mtime=1)
    build = MagicMock()
    monkeypatch.setattr("pilot.utils.cli_root", lambda: cli_root)
    monkeypatch.setattr("admin.backend.frontend.build_admin_frontend", build)

    BenchRuntime(make_bench(tmp_path))._ensure_admin_dist(lambda _message: None)

    build.assert_called_once_with(on_progress=ANY)
