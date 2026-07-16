"""Tests for pilot.core.app_dependency_installer.AppDependencyInstaller."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.app_dependency_installer import AppDependencyInstaller
from pilot.integrations.marketplace import Marketplace, Resolver
from pilot.exceptions import BenchError, DependencyResolutionError, RegistryUnavailableError
from tests.pilot.commands.test_commands import make_bench
from tests.pilot.commands.test_get_app import make_resolver


def make_app(bench, name: str):
    from pilot.config.app_config import AppConfig
    from pilot.core.app import App

    return App(AppConfig(name=name, repo=f"https://github.com/frappe/{name}", branch="main"), bench)


def test_install_returns_empty_when_app_not_in_marketplace_and_no_required_apps(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    app = make_app(bench, "custom_app")
    (bench.apps_path / "custom_app").mkdir(parents=True)
    (bench.apps_path / "custom_app" / "pyproject.toml").write_text('[project]\nname = "custom_app"\n')

    with patch.object(Marketplace, "read_all_apps", return_value=[]), \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"):
        result = AppDependencyInstaller(bench, app).install()

    assert result == []


def test_install_installs_missing_dependency(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    app = make_app(bench, "helpdesk")

    telephony = make_resolver("telephony")
    helpdesk = make_resolver("helpdesk", deps={"telephony": ""})
    helpdesk._registry = {"telephony": [telephony]}

    with patch.object(Marketplace, "read_all_apps", return_value=[helpdesk]), \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"), \
            patch("pilot.commands.apps.download.GetAppCommand") as mock_cmd:
        result = AppDependencyInstaller(bench, app).install()

    mock_cmd.assert_called_once_with(
        bench, telephony.repo, telephony.target, install_dependencies=False, skip_validations=True
    )
    mock_cmd.return_value.run.assert_called_once()
    assert result == []  # dep wasn't actually created on disk by the mocked run()


def test_install_skips_already_installed_dependency(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "telephony").mkdir(parents=True)
    (bench.sites_path / "apps.txt").write_text("frappe\ntelephony\n")
    app = make_app(bench, "helpdesk")

    telephony = make_resolver("telephony")
    helpdesk = make_resolver("helpdesk", deps={"telephony": ""})
    helpdesk._registry = {"telephony": [telephony]}

    with patch.object(Marketplace, "read_all_apps", return_value=[helpdesk]), \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"), \
            patch("pilot.commands.apps.download.GetAppCommand") as mock_cmd:
        result = AppDependencyInstaller(bench, app).install()

    mock_cmd.assert_not_called()
    assert [a.config.name for a in result] == ["telephony"]


def test_dependency_apps_falls_back_to_direct_deps_on_transitive_conflict(tmp_path: Path) -> None:
    """Regression: telephony is already installed (_install_missing's direct-
    deps check short-circuits before ever calling resolve()), but a deeper
    transitive conflict makes the full-chain resolve() raise when
    _dependency_apps calls it. That must not wipe out telephony from the
    result — it's still a real, already-installed dependency."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "telephony").mkdir(parents=True)
    (bench.sites_path / "apps.txt").write_text("frappe\ntelephony\n")
    app = make_app(bench, "helpdesk")

    telephony = make_resolver("telephony")
    helpdesk = make_resolver("helpdesk", deps={"telephony": ""})
    helpdesk._registry = {"telephony": [telephony]}

    with patch.object(Marketplace, "read_all_apps", return_value=[helpdesk]), \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"), \
            patch.object(Resolver, "resolve", side_effect=DependencyResolutionError("conflict deep in the graph")):
        result = AppDependencyInstaller(bench, app).install()

    assert [a.config.name for a in result] == ["telephony"]


def test_install_propagates_registry_unavailable_instead_of_swallowing_as_not_found(tmp_path: Path) -> None:
    """P1 regression: a tampered/corrupted registry cache raising
    RegistryUnavailableError during Marketplace construction must propagate —
    it must NOT be treated as "app not in registry" (which would silently
    skip dependency installation and leave a potentially broken install)."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    app = make_app(bench, "helpdesk")

    with patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"), \
            patch.object(Marketplace, "_read_apps_json", side_effect=RegistryUnavailableError("tampered")):
        with pytest.raises(RegistryUnavailableError):
            AppDependencyInstaller(bench, app).install()


def test_install_raises_when_app_not_in_marketplace_but_requires_missing_apps(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    app_dir = bench.apps_path / "india_compliance"
    app_dir.mkdir(parents=True)
    app_dir.joinpath("pyproject.toml").write_text(
        '[project]\nname = "india_compliance"\n\n[tool.bench.frappe-dependencies]\nerpnext = ">=15"\n'
    )
    app = make_app(bench, "india_compliance")

    with patch.object(Marketplace, "read_all_apps", return_value=[]), \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"):
        with pytest.raises(BenchError, match="isn't in the marketplace registry"):
            AppDependencyInstaller(bench, app).install()
