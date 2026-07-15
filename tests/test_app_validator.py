"""Tests for Validator's pre-install static checks on a cloned app."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from pilot.config.app_config import AppConfig
from pilot.core.app import App
from pilot.core.app_validator import Validator
from pilot.core.app_validator.dependency_declarations import DependencyDeclarationsCheck
from pilot.core.app_validator.imports import ImportCheck
from pilot.core.app_validator.repo_structure import RepoStructureCheck
from pilot.core.app_validator.syntax import SyntaxCheck
from pilot.exceptions import AppValidationError


@dataclass
class _FakeBench:
    apps_path: Path
    env_path: Path


def _make_app(bench_root: Path, name: str, pyproject: str, files: dict[str, str]) -> App:
    app_path = bench_root / "apps" / name
    app_path.mkdir(parents=True)
    (app_path / "pyproject.toml").write_text(pyproject)
    for relpath, content in files.items():
        full = app_path / relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    bench = _FakeBench(apps_path=bench_root / "apps", env_path=bench_root / "env")
    return App(AppConfig(name=name, repo=f"https://example.com/{name}.git", branch="main"), bench)


def _static_checks() -> list:
    """RepoStructureCheck, SyntaxCheck and DependencyDeclarationsCheck only —
    excludes ImportCheck, which installs into a real throwaway venv and needs
    a real frappe checkout, making it an integration concern, not a unit test."""
    return [RepoStructureCheck(), SyntaxCheck(), DependencyDeclarationsCheck()]


_SETUPTOOLS_BUILD = '[build-system]\nrequires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n'


def _make_fake_frappe(bench_root: Path) -> None:
    """A minimal, locally-buildable stand-in for the real frappe package,
    installed into the bench's apps/ dir so TmpEnv.create() has something
    real to pip install without needing network access to PyPI for frappe."""
    frappe_path = bench_root / "apps" / "frappe"
    frappe_path.mkdir(parents=True)
    (frappe_path / "pyproject.toml").write_text(
        f'[project]\nname = "frappe"\nversion = "0.0.1"\n\n{_SETUPTOOLS_BUILD}'
    )
    (frappe_path / "frappe").mkdir()
    (frappe_path / "frappe" / "__init__.py").write_text("")


def test_validate_passes_for_well_formed_app(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\ndependencies = ["requests>=2"]\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "import requests\nfrom myapp.hooks import app_name\n",
        },
    )
    Validator(app, checks=_static_checks()).validate()


def test_validate_includes_import_check_by_default(tmp_path: Path) -> None:
    app = _make_app(tmp_path, "myapp", '[project]\nname = "myapp"\n', {"myapp/hooks.py": ""})
    assert any(isinstance(check, ImportCheck) for check in Validator(app).checks)


def test_validate_repo_structure_fails_without_pyproject(tmp_path: Path) -> None:
    app_path = tmp_path / "apps" / "myapp"
    app_path.mkdir(parents=True)
    bench = _FakeBench(apps_path=tmp_path / "apps", env_path=tmp_path / "env")
    app = App(AppConfig(name="myapp", repo="https://example.com/myapp.git", branch="main"), bench)
    with pytest.raises(AppValidationError, match="pyproject.toml"):
        Validator(app).validate()


def test_validate_repo_structure_fails_without_hooks(tmp_path: Path) -> None:
    app = _make_app(tmp_path, "myapp", '[project]\nname = "myapp"\n', {"myapp/__init__.py": ""})
    with pytest.raises(AppValidationError, match="hooks.py"):
        Validator(app).validate()


def test_validate_syntax_fails_on_broken_python_file(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "def broken(:\n    pass\n",
        },
    )
    with pytest.raises(AppValidationError, match="syntax errors"):
        Validator(app).validate()


def test_dependency_declarations_passes_when_required_app_is_declared(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n\n[tool.bench.frappe-dependencies]\nerpnext = ">=15"\n',
        {"myapp/hooks.py": 'required_apps = ["frappe/erpnext"]\n'},
    )
    Validator(app, checks=_static_checks()).validate()


def test_dependency_declarations_fails_when_required_app_is_missing(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n\n[tool.bench.frappe-dependencies]\nfrappe = ">=15"\n',
        {"myapp/hooks.py": 'required_apps = ["frappe/erpnext"]\n'},
    )
    with pytest.raises(AppValidationError, match="erpnext"):
        Validator(app, checks=_static_checks()).validate()


def test_import_check_passes_when_all_imports_resolve(tmp_path: Path) -> None:
    _make_fake_frappe(tmp_path)
    app = _make_app(
        tmp_path,
        "myapp",
        f'[project]\nname = "myapp"\nversion = "0.0.1"\ndependencies = ["frappe"]\n\n{_SETUPTOOLS_BUILD}',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "import frappe\nfrom myapp.hooks import app_name\n",
        },
    )
    ImportCheck().run(app)


def test_import_check_fails_on_genuinely_missing_import(tmp_path: Path) -> None:
    _make_fake_frappe(tmp_path)
    app = _make_app(
        tmp_path,
        "myapp",
        f'[project]\nname = "myapp"\nversion = "0.0.1"\ndependencies = ["frappe"]\n\n{_SETUPTOOLS_BUILD}',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "import definitely_missing_package_xyz\n",
        },
    )
    with pytest.raises(AppValidationError, match="definitely_missing_package_xyz"):
        ImportCheck().run(app)


def test_import_check_resolves_external_package_published_under_different_dist_name(
    tmp_path: Path,
) -> None:
    """beautifulsoup4 is published on PyPI under that dist name but installs
    an import-name folder of `bs4` — proves resolution goes off what's
    actually on disk in site-packages, not the pyproject dependency string."""
    _make_fake_frappe(tmp_path)
    app = _make_app(
        tmp_path,
        "myapp",
        (
            '[project]\nname = "myapp"\nversion = "0.0.1"\n'
            f'dependencies = ["frappe", "beautifulsoup4"]\n\n{_SETUPTOOLS_BUILD}'
        ),
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "import bs4\nfrom bs4 import BeautifulSoup\n",
        },
    )
    ImportCheck().run(app)


def _modules_for(app: App, relpath: str, source: str) -> set[str]:
    full = app.path / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(source)
    return {module for module, _lineno in ImportCheck()._file_imported_modules(app, full)}


def test_import_check_skips_stdlib_imports(tmp_path: Path) -> None:
    # Stdlib filtering happens in _imported_module_locations (across all
    # files), not per-file — os/sys/json show up in the raw per-file parse.
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {"myapp/hooks.py": "", "myapp/utils.py": "import os\nimport sys\nimport json\n"},
    )
    assert ImportCheck()._imported_module_locations(app) == {}


def test_import_check_resolves_bare_relative_import_at_package_root(tmp_path: Path) -> None:
    app = _make_app(tmp_path, "myapp", '[project]\nname = "myapp"\n', {"myapp/hooks.py": ""})
    modules = _modules_for(app, "myapp/utils.py", "from . import hooks\n")
    assert modules == {"myapp"}


def test_import_check_resolves_relative_import_with_module(tmp_path: Path) -> None:
    app = _make_app(tmp_path, "myapp", '[project]\nname = "myapp"\n', {"myapp/hooks.py": ""})
    modules = _modules_for(app, "myapp/sub/mod.py", "from .. import other\n")
    assert modules == {"myapp"}
    modules = _modules_for(app, "myapp/sub/mod.py", "from ..sibling import thing\n")
    assert modules == {"myapp.sibling"}


def test_import_check_raises_on_relative_import_beyond_top_level_package(tmp_path: Path) -> None:
    app = _make_app(tmp_path, "myapp", '[project]\nname = "myapp"\n', {"myapp/hooks.py": ""})
    with pytest.raises(AppValidationError, match="invalid relative import"):
        _modules_for(app, "myapp/hooks.py", "from .. import x\n")


def test_import_check_skips_imports_inside_any_try_except(tmp_path: Path) -> None:
    app = _make_app(tmp_path, "myapp", '[project]\nname = "myapp"\n', {"myapp/hooks.py": ""})
    source = (
        "try:\n"
        "    import definitely_missing_a\n"
        "except ImportError:\n"
        "    pass\n"
        "try:\n"
        "    import definitely_missing_b\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        "    import definitely_missing_c\n"
        "except:\n"
        "    pass\n"
        "import required_dependency\n"
    )
    assert _modules_for(app, "myapp/utils.py", source) == {"required_dependency"}


def test_import_check_skips_type_checking_only_imports(tmp_path: Path) -> None:
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import only_needed_for_types\n"
        "import required_dependency\n"
    )
    app = _make_app(
        tmp_path, "myapp", '[project]\nname = "myapp"\n', {"myapp/hooks.py": "", "myapp/utils.py": source}
    )
    # `typing` itself is stdlib, filtered out at the _imported_module_locations level.
    assert list(ImportCheck()._imported_module_locations(app)) == ["required_dependency"]


def test_import_check_error_reports_source_location(tmp_path: Path) -> None:
    _make_fake_frappe(tmp_path)
    app = _make_app(
        tmp_path,
        "myapp",
        f'[project]\nname = "myapp"\nversion = "0.0.1"\ndependencies = ["frappe"]\n\n{_SETUPTOOLS_BUILD}',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/deep/nested.py": "\n\nimport definitely_missing_package_xyz\n",
        },
    )
    with pytest.raises(AppValidationError, match=r"imported at: .*deep/nested\.py:3"):
        ImportCheck().run(app)


def test_import_check_skips_test_files(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "",
            "myapp/test_utils.py": "import dev_only_dependency\n",
            "myapp/conftest.py": "import dev_only_dependency\n",
        },
    )
    check = ImportCheck()
    assert check._imported_module_locations(app) == {}
