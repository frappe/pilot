"""Unit tests for bench-cli command classes."""
from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bench_cli.config.app_config import AppConfig
from bench_cli.config.bench_config import BenchConfig
from bench_cli.config.mariadb_config import MariaDBConfig
from bench_cli.config.redis_config import RedisConfig
from bench_cli.config.worker_config import WorkerConfig, WorkerGroup
from bench_cli.core.bench import Bench
from bench_cli.exceptions import BenchError


def make_bench(tmp_path: Path) -> Bench:
    config = BenchConfig(
        name="test-bench",
        python_version="3.14",
        apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(groups=[
            WorkerGroup(queues=["default"], count=2),
            WorkerGroup(queues=["short"], count=1),
            WorkerGroup(queues=["long"], count=1),
        ]),
    )
    return Bench(config, tmp_path)


# ── NewCommand ────────────────────────────────────────────────────────────────


def test_new_command_creates_directory_and_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bench_cli.commands.new import NewCommand

    monkeypatch.setattr("builtins.input", lambda _: "")
    target = tmp_path / "benches" / "my-bench"
    NewCommand(target, "my-bench").run()

    assert target.is_dir()
    content = (target / "bench.toml").read_text()
    assert 'name = "my-bench"' in content


def test_new_command_raises_if_bench_already_exists(tmp_path: Path) -> None:
    from bench_cli.commands.new import NewCommand

    target = tmp_path / "benches" / "my-bench"
    target.mkdir(parents=True)
    (target / "bench.toml").write_text("[bench]\n")

    with pytest.raises(BenchError, match="already exists"):
        NewCommand(target, "my-bench").run()


def test_new_command_creates_benches_dir_if_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bench_cli.commands.new import NewCommand

    monkeypatch.setattr("builtins.input", lambda _: "")
    target = tmp_path / "benches" / "fresh"
    assert not target.parent.exists()
    NewCommand(target, "fresh").run()
    assert target.parent.is_dir()


def test_new_command_first_bench_uses_default_ports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bench_cli.commands.new import NewCommand

    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(NewCommand, "_port_is_live", staticmethod(lambda port: False))
    target = tmp_path / "benches" / "my-bench"
    NewCommand(target, "my-bench").run()

    with open(target / "bench.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["bench"]["http_port"] == 8000
    assert data["admin"]["port"] == 7000


def test_new_command_second_bench_gets_next_offset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every port field must shift by the same offset — a regression guard
    for a bug where admin_port got the offset applied twice."""
    from bench_cli.commands.new import NewCommand

    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(NewCommand, "_port_is_live", staticmethod(lambda port: False))
    benches_dir = tmp_path / "benches"
    NewCommand(benches_dir / "first", "first").run()
    NewCommand(benches_dir / "second", "second").run()

    with open(benches_dir / "second" / "bench.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["bench"]["http_port"] == 8001
    assert data["bench"]["socketio_port"] == 9001
    assert data["redis"]["cache_port"] == 13001
    assert data["redis"]["queue_port"] == 11001
    assert data["admin"]["port"] == 7001


def test_new_command_writes_dedicated_mariadb_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """New benches default to their own MariaDB instance with an isolated
    socket/datadir and an offset port."""
    from bench_cli.commands.new import NewCommand

    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(NewCommand, "_port_is_live", staticmethod(lambda port: False))
    monkeypatch.setattr("bench_cli.commands.new.is_linux", lambda: True)
    benches_dir = tmp_path / "benches"
    NewCommand(benches_dir / "first", "first").run()
    NewCommand(benches_dir / "second", "second").run()

    with open(benches_dir / "second" / "bench.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["mariadb"]["instance"] == "second"
    assert data["mariadb"]["socket_path"] == "/run/mysqld/mysqld-second.sock"
    assert data["mariadb"]["data_dir"] == "/var/lib/mysql-second"
    assert data["mariadb"]["port"] == 3307  # base 3306 + offset 1


def test_volume_setup_mounts_dataset_at_instance_datadir(tmp_path: Path) -> None:
    """For an instance bench, the ZFS mariadb dataset mounts at the instance's
    sibling datadir (not the shared /var/lib/mysql)."""
    from bench_cli.commands.volume import VolumeSetupCommand

    data = {
        "bench": {"name": "shop", "python": "3.14"},
        "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "develop"}],
        "mariadb": {
            "root_password": "root",
            "instance": "shop",
            "data_dir": "/var/lib/mysql-shop",
            "socket_path": "/run/mysqld/mysqld-shop.sock",
        },
        "redis": {"cache_port": 13000, "queue_port": 11000},
        "volume": {"enabled": True, "pool": "shop-pool"},
    }
    config = BenchConfig._from_dict(data)
    cmd = VolumeSetupCommand(config.volume, tmp_path / "shop", bench_config=config)

    volume_manager = MagicMock()
    cmd.setup_mariadb(volume_manager)

    volume_manager.set_mountpoint.assert_called_once()
    dataset, mountpoint = volume_manager.set_mountpoint.call_args[0]
    assert dataset == config.volume.mariadb_dataset  # shop-pool/mariadb
    assert str(mountpoint) == "/var/lib/mysql-shop"


def test_new_command_skips_offset_with_live_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An orphaned process holding a port with no matching bench.toml must
    also be avoided, not just offsets already on disk."""
    from bench_cli.commands.new import NewCommand

    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(NewCommand, "_port_is_live", staticmethod(lambda port: port == 8000))

    target = tmp_path / "benches" / "my-bench"
    NewCommand(target, "my-bench").run()

    with open(target / "bench.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["bench"]["http_port"] == 8001


# ── NewSiteCommand ────────────────────────────────────────────────────────────


def test_new_site_raises_if_site_exists(tmp_path: Path) -> None:
    from bench_cli.commands.new_site import NewSiteCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    site_dir = bench.sites_path / "site1.localhost"
    site_dir.mkdir()
    (site_dir / "site_config.json").write_text("{}")

    with pytest.raises(BenchError, match="already exists"):
        NewSiteCommand(bench, "site1.localhost", ["frappe"])._validate()


def test_new_site_raises_if_app_not_in_apps_txt(tmp_path: Path) -> None:
    from bench_cli.commands.new_site import NewSiteCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.sites_path / "apps.txt").write_text("frappe\n")

    with pytest.raises(BenchError, match="erpnext"):
        NewSiteCommand(bench, "site1.localhost", ["erpnext"])._validate()


def test_new_site_validate_passes_when_all_ok(tmp_path: Path) -> None:
    from bench_cli.commands.new_site import NewSiteCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.sites_path / "apps.txt").write_text("frappe\n")

    NewSiteCommand(bench, "site1.localhost", ["frappe"])._validate()  # no raise


def test_new_site_validate_passes_with_no_apps_requested(tmp_path: Path) -> None:
    from bench_cli.commands.new_site import NewSiteCommand

    bench = make_bench(tmp_path)
    bench.create_directories()

    NewSiteCommand(bench, "site1.localhost", [])._validate()  # no raise


# ── RemoveAppCommand ──────────────────────────────────────────────────────────


def test_remove_app_raises_when_app_directory_missing(tmp_path: Path) -> None:
    from bench_cli.commands.remove_app import RemoveAppCommand

    bench = make_bench(tmp_path)
    bench.create_directories()

    with pytest.raises(BenchError, match="not found"):
        RemoveAppCommand(bench, "nonexistent")._validate()


def test_remove_app_raises_when_removing_framework_app(tmp_path: Path) -> None:
    from bench_cli.commands.remove_app import RemoveAppCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "frappe").mkdir()

    with pytest.raises(BenchError, match="framework"):
        RemoveAppCommand(bench, "frappe")._validate()


def test_remove_app_confirm_raises_on_negative_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bench_cli.commands.remove_app import RemoveAppCommand

    bench = make_bench(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: "n")

    with pytest.raises(BenchError, match="Aborted"):
        RemoveAppCommand(bench, "myapp")._confirm()


def test_remove_app_confirm_passes_on_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bench_cli.commands.remove_app import RemoveAppCommand

    bench = make_bench(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: "y")
    RemoveAppCommand(bench, "myapp")._confirm()  # no raise


def test_remove_app_confirm_skipped_when_skip_confirm(tmp_path: Path) -> None:
    from bench_cli.commands.remove_app import RemoveAppCommand

    bench = make_bench(tmp_path)
    RemoveAppCommand(bench, "myapp", skip_confirm=True)._confirm()  # no raise, no input


def test_remove_app_removes_app_from_apps_txt(tmp_path: Path) -> None:
    from bench_cli.commands.remove_app import RemoveAppCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    apps_txt = bench.sites_path / "apps.txt"
    apps_txt.write_text("frappe\nmyapp\nerpnext\n")

    RemoveAppCommand(bench, "myapp")._remove_from_apps_txt()

    lines = [l for l in apps_txt.read_text().splitlines() if l.strip()]
    assert "myapp" not in lines
    assert "frappe" in lines
    assert "erpnext" in lines


def test_remove_app_removes_from_apps_txt_missing_file(tmp_path: Path) -> None:
    from bench_cli.commands.remove_app import RemoveAppCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    # apps.txt does not exist — should not raise

    RemoveAppCommand(bench, "myapp")._remove_from_apps_txt()


def test_remove_app_deletes_app_directory(tmp_path: Path) -> None:
    from bench_cli.commands.remove_app import RemoveAppCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "myapp"
    app_dir.mkdir()
    (app_dir / "setup.py").write_text("")

    RemoveAppCommand(bench, "myapp")._delete_app_dir()

    assert not app_dir.exists()


def test_remove_app_full_flow_no_sites(tmp_path: Path) -> None:
    from bench_cli.commands.remove_app import RemoveAppCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "erpnext"
    app_dir.mkdir()
    (bench.sites_path / "apps.txt").write_text("frappe\nerpnext\n")

    cmd = RemoveAppCommand(bench, "erpnext", skip_confirm=True)
    with patch("bench_cli.managers.python_env_manager.PythonEnvManager.uninstall_app"):
        cmd.run()

    assert not app_dir.exists()
    remaining = [l for l in (bench.sites_path / "apps.txt").read_text().splitlines() if l.strip()]
    assert "erpnext" not in remaining


# ── UninstallAppCommand ───────────────────────────────────────────────────────


def test_uninstall_app_raises_if_site_not_found(tmp_path: Path) -> None:
    from bench_cli.commands.uninstall_app import UninstallAppCommand

    bench = make_bench(tmp_path)
    bench.create_directories()

    with pytest.raises(BenchError, match="does not exist"):
        UninstallAppCommand(bench, "site1.localhost", "myapp").run()


def test_uninstall_app_raises_if_app_not_installed(tmp_path: Path) -> None:
    from bench_cli.commands.uninstall_app import UninstallAppCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    site_dir = bench.sites_path / "site1.localhost"
    site_dir.mkdir()
    (site_dir / "site_config.json").write_text("{}")

    cmd = UninstallAppCommand(bench, "site1.localhost", "myapp")
    with patch("bench_cli.core.site.Site.list_apps", return_value=["frappe"]):
        with pytest.raises(BenchError, match="not installed"):
            cmd.run()


def test_uninstall_app_calls_site_uninstall_when_installed(tmp_path: Path) -> None:
    from bench_cli.commands.uninstall_app import UninstallAppCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    site_dir = bench.sites_path / "site1.localhost"
    site_dir.mkdir()
    (site_dir / "site_config.json").write_text("{}")

    cmd = UninstallAppCommand(bench, "site1.localhost", "myapp")
    with patch("bench_cli.core.site.Site.list_apps", return_value=["frappe", "myapp"]), \
         patch("bench_cli.core.site.Site.uninstall_app") as mock_uninstall:
        cmd.run()
        mock_uninstall.assert_called_once_with("myapp")


# ── FrappeCommand ─────────────────────────────────────────────────────────────


def test_frappe_command_raises_if_venv_python_missing(tmp_path: Path) -> None:
    from bench_cli.commands.frappe_cmd import FrappeCommand

    bench = make_bench(tmp_path)

    with pytest.raises(BenchError, match="not found"):
        FrappeCommand(bench).run_raw(["frappe", "migrate"])


def test_frappe_command_calls_subprocess_with_frappe_call(tmp_path: Path) -> None:
    from bench_cli.commands.frappe_cmd import FrappeCommand

    bench = make_bench(tmp_path)
    (tmp_path / "env" / "bin").mkdir(parents=True)
    (tmp_path / "env" / "bin" / "python").touch()

    mock_result = MagicMock(returncode=0)
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        with pytest.raises(SystemExit) as exc_info:
            FrappeCommand(bench).run_raw(["frappe", "migrate"])
        assert exc_info.value.code == 0
        called_args = mock_run.call_args[0][0]
        assert "frappe.utils.bench_helper" in " ".join(called_args)
        assert "frappe" in called_args
        assert "migrate" in called_args


def test_frappe_command_exits_with_subprocess_returncode(tmp_path: Path) -> None:
    from bench_cli.commands.frappe_cmd import FrappeCommand

    bench = make_bench(tmp_path)
    (tmp_path / "env" / "bin").mkdir(parents=True)
    (tmp_path / "env" / "bin" / "python").touch()

    with patch("subprocess.run", return_value=MagicMock(returncode=42)):
        with pytest.raises(SystemExit) as exc_info:
            FrappeCommand(bench).run_raw(["frappe", "foo"])
        assert exc_info.value.code == 42


# ── BuildCommand ──────────────────────────────────────────────────────────────


def test_build_command_force_calls_frappe_build(tmp_path: Path) -> None:
    from bench_cli.commands.build import BuildCommand

    bench = make_bench(tmp_path)
    bench.create_directories()

    with patch("bench_cli.managers.python_env_manager.PythonEnvManager.build_assets") as mock_build:
        BuildCommand(bench, force=True).run()
        mock_build.assert_called_once()


def test_build_command_default_uses_prebuilt_per_app(tmp_path: Path) -> None:
    from bench_cli.commands.build import BuildCommand

    bench = make_bench(tmp_path)
    bench.create_directories()

    with patch("bench_cli.managers.python_env_manager.PythonEnvManager.build_assets_for_app") as mock_build:
        with patch.object(bench, "apps", return_value=[]):
            BuildCommand(bench).run()
            mock_build.assert_not_called()  # no apps → nothing called


# ── SetupRequirementsCommand ──────────────────────────────────────────────────


def test_requirements_skips_app_without_python_setup_files(tmp_path: Path) -> None:
    from bench_cli.commands.setup.requirements import SetupRequirementsCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "bare-app"
    app_dir.mkdir()
    (app_dir / ".git").mkdir()
    # No pyproject.toml or setup.py

    with patch("bench_cli.managers.python_env_manager.PythonEnvManager._ensure_uv", return_value="uv"), \
         patch("bench_cli.utils.run_command") as mock_rc:
        SetupRequirementsCommand(bench)._install_python()
        mock_rc.assert_not_called()


def test_requirements_installs_app_with_pyproject_toml(tmp_path: Path) -> None:
    from bench_cli.commands.setup.requirements import SetupRequirementsCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "myapp"
    app_dir.mkdir()
    (app_dir / ".git").mkdir()
    (app_dir / "pyproject.toml").write_text("[project]\nname = 'myapp'\n")

    with patch("bench_cli.managers.python_env_manager.PythonEnvManager._ensure_uv", return_value="uv"), \
         patch("bench_cli.utils.run_command") as mock_rc:
        SetupRequirementsCommand(bench)._install_python()
        mock_rc.assert_called_once()


def test_requirements_installs_app_with_setup_py(tmp_path: Path) -> None:
    from bench_cli.commands.setup.requirements import SetupRequirementsCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "myapp"
    app_dir.mkdir()
    (app_dir / ".git").mkdir()
    (app_dir / "setup.py").write_text("from setuptools import setup; setup()\n")

    with patch("bench_cli.managers.python_env_manager.PythonEnvManager._ensure_uv", return_value="uv"), \
         patch("bench_cli.utils.run_command") as mock_rc:
        SetupRequirementsCommand(bench)._install_python()
        mock_rc.assert_called_once()


def test_requirements_skips_js_for_app_without_package_json(tmp_path: Path) -> None:
    from bench_cli.commands.setup.requirements import SetupRequirementsCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "myapp"
    app_dir.mkdir()
    (app_dir / ".git").mkdir()
    # No package.json

    with patch("bench_cli.utils.run_command") as mock_rc:
        SetupRequirementsCommand(bench)._install_js()
        mock_rc.assert_not_called()


def test_requirements_installs_js_for_app_with_package_json(tmp_path: Path) -> None:
    from bench_cli.commands.setup.requirements import SetupRequirementsCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "myapp"
    app_dir.mkdir()
    (app_dir / ".git").mkdir()
    (app_dir / "package.json").write_text('{"name": "myapp"}\n')

    with patch("bench_cli.utils.get_yarn_bin", return_value="yarn"):
        with patch("bench_cli.utils.run_command") as mock_rc:
            SetupRequirementsCommand(bench)._install_js()
            mock_rc.assert_called_once()
            assert mock_rc.call_args[0][0] == ["yarn", "install"]


# ── UpdateCommand ─────────────────────────────────────────────────────────────


def test_update_command_runs_all_steps(tmp_path: Path) -> None:
    from bench_cli.commands.update import UpdateCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    cmd = UpdateCommand(bench, skip_confirm=True)

    with patch.object(cmd, "_warn_if_running"), \
         patch.object(cmd, "_update_apps"), \
         patch.object(cmd, "_reinstall_apps"), \
         patch.object(cmd, "_migrate_sites"):
        cmd.run()


def test_update_command_skips_confirm_when_bench_not_running(tmp_path: Path) -> None:
    from bench_cli.commands.update import UpdateCommand

    bench = make_bench(tmp_path)
    bench.create_directories()
    cmd = UpdateCommand(bench, skip_confirm=False)

    with patch("bench_cli.managers.process_manager.ProcessManager.is_running", return_value=False):
        cmd._warn_if_running()  # no raise, no prompt


def test_update_command_update_apps_ignores_command_errors(tmp_path: Path) -> None:
    from bench_cli.commands.update import UpdateCommand
    from bench_cli.exceptions import CommandError

    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "myapp"
    app_dir.mkdir()
    (app_dir / ".git").mkdir()

    cmd = UpdateCommand(bench, skip_confirm=True)

    with patch("bench_cli.core.app.App.update", side_effect=CommandError("git error")):
        cmd._update_apps()  # should not raise


def test_update_command_migrate_sites_handles_failures(tmp_path: Path) -> None:
    from bench_cli.commands.update import UpdateCommand
    from bench_cli.exceptions import CommandError

    bench = make_bench(tmp_path)
    bench.create_directories()
    site_dir = bench.sites_path / "site1.localhost"
    site_dir.mkdir()
    (site_dir / "site_config.json").write_text("{}")

    cmd = UpdateCommand(bench, skip_confirm=True)

    with patch("bench_cli.core.site.Site.migrate", side_effect=CommandError("migrate failed")):
        with pytest.raises(SystemExit):
            cmd._migrate_sites()


# ── DropSiteCommand ───────────────────────────────────────────────────────────


def test_drop_site_removes_site_from_bench_toml(tmp_path: Path) -> None:
    import tomllib
    from bench_cli.commands.drop_site import DropSiteCommand

    bench = make_bench(tmp_path)
    bench_toml = tmp_path / "bench.toml"
    bench_toml.write_text(
        '[bench]\nname = "test-bench"\npython = "3.14"\n\n'
        "[[apps]]\nname = \"frappe\"\nrepo = \"...\"\nbranch = \"version-16\"\n\n"
        "[[sites]]\nname = \"site1.localhost\"\n\n"
        "[[sites]]\nname = \"site2.localhost\"\n\n"
        "[mariadb]\nhost = \"localhost\"\nport = 3306\nroot_password = \"root\"\n\n"
        "[redis]\nport = 13000\n\n"
        '[[workers]]\nqueues = ["default", "short", "long"]\ncount = 1\n'
    )

    cmd = DropSiteCommand(bench, "site1.localhost")
    cmd._remove_from_bench_toml()

    with bench_toml.open("rb") as fh:
        raw = tomllib.load(fh)
    names = [s.get("name") for s in raw.get("sites", [])]
    assert "site1.localhost" not in names
    assert "site2.localhost" in names


def test_drop_site_removes_from_toml_when_no_sites_key(tmp_path: Path) -> None:
    from bench_cli.commands.drop_site import DropSiteCommand

    bench = make_bench(tmp_path)
    bench_toml = tmp_path / "bench.toml"
    bench_toml.write_text(
        '[bench]\nname = "test-bench"\npython = "3.14"\n\n'
        "[[apps]]\nname = \"frappe\"\nrepo = \"...\"\nbranch = \"version-16\"\n\n"
        "[mariadb]\nhost = \"localhost\"\nport = 3306\nroot_password = \"root\"\n\n"
        "[redis]\nport = 13000\n\n"
        '[[workers]]\nqueues = ["default", "short", "long"]\ncount = 1\n'
    )

    cmd = DropSiteCommand(bench, "nonexistent")
    cmd._remove_from_bench_toml()  # no raise
