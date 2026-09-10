"""Tests for Validator's pre-install static checks on a cloned app."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from pilot.config import AppConfig
from pilot.core.app import App
from pilot.core.app.validator import Validator
from pilot.core.app.validator.dependency_declarations import DependencyDeclarationsCheck
from pilot.core.app.validator.fixtures import FixturesCheck
from pilot.core.app.validator.frappe_compatibility import FrappeCompatibilityCheck
from pilot.core.app.validator.hooks import HooksCheck
from pilot.core.app.validator.imports import ImportCheck
from pilot.core.app.validator.repo_structure import RepoStructureCheck
from pilot.core.app.validator.symlinks import SymlinkCheck
from pilot.core.app.validator.syntax import SyntaxCheck
from pilot.core.app.validator.version_specifiers import VersionSpecifiersCheck
from pilot.exceptions import AppValidationError


@dataclass
class _FakeBench:
    apps_path: Path
    env_path: Path

    @property
    def python(self) -> Path:
        return Path(sys.executable)

    def apps(self) -> list[App]:
        apps = []
        for child in self.apps_path.iterdir():
            if child.is_dir() and (child / "pyproject.toml").exists():
                apps.append(self.app(child.name))
        return apps

    def app(self, name: str) -> App:
        return App(AppConfig(name=name, repo=f"https://example.com/{name}.git", branch="main"), self)


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
    """Static checks only; ImportCheck needs a real throwaway venv."""
    return [
        RepoStructureCheck(),
        VersionSpecifiersCheck(),
        SyntaxCheck(),
        HooksCheck(),
        FixturesCheck(),
        DependencyDeclarationsCheck(),
    ]


_SETUPTOOLS_BUILD = '[build-system]\nrequires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n'


def _make_fake_frappe(bench_root: Path) -> None:
    """Create a local fake frappe package for TmpEnv tests."""
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
        '[project]\nname = "myapp"\ndependencies = ["requests>=2"]\n\n'
        '[tool.bench.frappe-dependencies]\nfrappe = ">=15"\n',
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
    with pytest.raises(AppValidationError, match=r"pyproject\.toml"):
        Validator(app).validate()


def test_validate_repo_structure_fails_without_hooks(tmp_path: Path) -> None:
    app = _make_app(tmp_path, "myapp", '[project]\nname = "myapp"\n', {"myapp/__init__.py": ""})
    with pytest.raises(AppValidationError, match=r"hooks\.py"):
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
        '[project]\nname = "myapp"\n\n[tool.bench.frappe-dependencies]\nfrappe = ">=15"\nerpnext = ">=15"\n',
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


def test_dependency_declarations_fails_when_frappe_dependencies_missing_entirely(
    tmp_path: Path,
) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},
    )
    with pytest.raises(AppValidationError, match="must declare the frappe versions it supports"):
        Validator(app, checks=_static_checks()).validate()


def test_dependency_declarations_fails_when_frappe_dependencies_omits_frappe(
    tmp_path: Path,
) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n\n[tool.bench.frappe-dependencies]\nerpnext = ">=15"\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},
    )
    with pytest.raises(AppValidationError, match="must declare the frappe versions it supports"):
        Validator(app, checks=_static_checks()).validate()


def test_dependency_declarations_excludes_frappe_from_hooks_comparison(tmp_path: Path) -> None:
    """pyproject always declares frappe; hooks.py's required_apps never does -
    frappe being present in pyproject alone must not cause any false failure."""
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n\n[tool.bench.frappe-dependencies]\nfrappe = ">=15"\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},  # no required_apps at all
    )
    Validator(app, checks=_static_checks()).validate()


def test_dependency_declarations_skips_the_frappe_app_itself(tmp_path: Path) -> None:
    """frappe has no [tool.bench.frappe-dependencies] - it is the dependency."""
    app = _make_app(
        tmp_path, "frappe", '[project]\nname = "frappe"\n', {"frappe/hooks.py": "app_name = 'frappe'\n"}
    )
    Validator(app, checks=_static_checks()).validate()


def test_dependency_declarations_accepts_a_prerelease_version_range(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n\n'
        '[tool.bench.frappe-dependencies]\nfrappe = ">=16.0.0-dev,<=17.0.0-dev"\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},
    )
    Validator(app, checks=_static_checks()).validate()


def test_dependency_declarations_fails_on_an_invalid_version_specifier(tmp_path: Path) -> None:
    """A missing comma is the same defect uv rejects in [project] dependencies."""
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n\n'
        '[tool.bench.frappe-dependencies]\nfrappe = ">=16.0.0-dev <17.0.0-dev"\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},
    )
    with pytest.raises(AppValidationError, match="declares an invalid version for 'frappe'"):
        Validator(app, checks=_static_checks()).validate()


def test_dependency_declarations_fails_on_an_unpinned_version(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n\n[tool.bench.frappe-dependencies]\nfrappe = ""\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},
    )
    with pytest.raises(AppValidationError, match="declares 'frappe' with no version"):
        Validator(app, checks=_static_checks()).validate()


def test_dependency_declarations_rejects_a_list_of_apps_without_versions(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n\n[tool.bench]\nfrappe-dependencies = ["frappe", "erpnext"]\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},
    )
    with pytest.raises(AppValidationError, match="expected a table of versions, got list"):
        DependencyDeclarationsCheck().get_frappe_dependencies(app)


def test_frappe_dependencies_fails_cleanly_without_a_pyproject(tmp_path: Path) -> None:
    """The dependency installer calls this before RepoStructureCheck has run, so
    a missing file has to be a validation error, not a FileNotFoundError."""
    app_path = tmp_path / "apps" / "myapp"
    (app_path / "myapp").mkdir(parents=True)
    bench = _FakeBench(apps_path=tmp_path / "apps", env_path=tmp_path / "env")
    app = App(AppConfig(name="myapp", repo="https://example.com/myapp.git", branch="main"), bench)

    with pytest.raises(AppValidationError, match=r"has no pyproject\.toml"):
        DependencyDeclarationsCheck().get_frappe_dependencies(app)


def test_checks_report_broken_toml_instead_of_raising_tomllib(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project\nname = "myapp"\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},
    )
    with pytest.raises(AppValidationError, match=r"invalid pyproject\.toml"):
        VersionSpecifiersCheck().run(app)


def test_version_specifiers_fails_on_invalid_requires_python(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\nrequires-python = ">=20.19 <21"\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},
    )
    with pytest.raises(AppValidationError, match="invalid requires-python"):
        Validator(app, checks=_static_checks()).validate()


def test_version_specifiers_fails_on_invalid_dependency_specifier(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\ndependencies = ["frappe >=20.19 <21"]\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},
    )
    with pytest.raises(AppValidationError, match="invalid dependency"):
        Validator(app, checks=_static_checks()).validate()


def test_version_specifiers_passes_for_valid_specs(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\nrequires-python = ">=20.19,<21"\n'
        'dependencies = ["requests>=2,<3"]\n'
        '[project.optional-dependencies]\ndev = ["pytest>=7"]\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},
    )
    Validator(app, checks=[RepoStructureCheck(), VersionSpecifiersCheck()]).validate()


def _make_hooks_app(tmp_path: Path, hooks: str, **extra_files: str) -> App:
    files = {"myapp/hooks.py": hooks, "myapp/__init__.py": ""}
    files.update({f"myapp/{name.replace('__', '/')}.py": source for name, source in extra_files.items()})
    return _make_app(tmp_path, "myapp", '[project]\nname = "myapp"\n', files)


def test_hooks_passes_for_well_formed_hooks(tmp_path: Path) -> None:
    app = _make_hooks_app(
        tmp_path,
        'required_apps = ["erpnext"]\n'
        'boot_session = "myapp.startup.boot"\n'
        'doc_events = {"ToDo": {"on_update": ["myapp.overrides.on_update"]}}\n'
        'override_doctype_class = {"ToDo": "myapp.overrides.CustomToDo"}\n',
        startup="def boot():\n    pass\n",
        overrides="def on_update():\n    pass\n\n\nclass CustomToDo:\n    pass\n",
    )
    HooksCheck().run(app)


def test_hooks_allows_bare_string_for_list_hooks(tmp_path: Path) -> None:
    """frappe's append_hook listifies non-dict values, so a bare string is valid."""
    HooksCheck().run(_make_hooks_app(tmp_path, 'required_apps = "erpnext"\nfixtures = "DocType"\n'))


def test_hooks_fails_when_dict_hook_is_not_a_dict(tmp_path: Path) -> None:
    app = _make_hooks_app(tmp_path, 'doc_events = ["myapp.overrides.on_update"]\n')
    with pytest.raises(AppValidationError, match="doc_events must be a dict"):
        HooksCheck().run(app)


def test_hooks_allows_a_dict_hook_built_elsewhere(tmp_path: Path) -> None:
    """A name or a call may evaluate to a dict at import time - only a literal of
    the wrong shape can be judged from the source."""
    HooksCheck().run(_make_hooks_app(tmp_path, "DOC_EVENTS = {}\ndoc_events = DOC_EVENTS\n"))
    HooksCheck().run(_make_hooks_app(tmp_path / "call", "doc_events = build_events()\n"))


def test_hooks_fails_when_path_points_at_missing_submodule(tmp_path: Path) -> None:
    app = _make_hooks_app(tmp_path, 'after_migrate = "myapp.setup.install.after_migrate"\n')
    with pytest.raises(AppValidationError, match="'myapp' has no 'setup'"):
        HooksCheck().run(app)


def test_hooks_fails_when_path_walks_into_a_non_package_directory(tmp_path: Path) -> None:
    app = _make_hooks_app(tmp_path, 'on_login = "myapp.public.js.on_login"\n')
    (app.path / "myapp" / "public").mkdir()
    with pytest.raises(AppValidationError, match=r"no module 'myapp\.public\.js\.on_login'"):
        HooksCheck().run(app)


def test_hooks_fails_when_path_points_at_missing_attribute(tmp_path: Path) -> None:
    app = _make_hooks_app(
        tmp_path, 'after_migrate = "myapp.setup.renamed"\n', setup="def original():\n    pass\n"
    )
    with pytest.raises(AppValidationError, match="'setup' has no 'renamed'"):
        HooksCheck().run(app)


def test_hooks_resolves_attribute_defined_in_package_init(tmp_path: Path) -> None:
    """`myapp.utils.helper` where utils is a package and helper lives in its __init__."""
    app = _make_hooks_app(tmp_path, 'on_login = "myapp.utils.helper"\n')
    (app.path / "myapp" / "utils").mkdir()
    (app.path / "myapp" / "utils" / "__init__.py").write_text("def helper():\n    pass\n")
    HooksCheck().run(app)


def test_hooks_resolves_path_into_a_sibling_installed_app(tmp_path: Path) -> None:
    app = _make_hooks_app(tmp_path, 'boot_session = "erpnext.startup.boot.boot_session"\n')
    boot = tmp_path / "apps" / "erpnext" / "erpnext" / "startup" / "boot.py"
    boot.parent.mkdir(parents=True)
    boot.write_text("def boot_session():\n    pass\n")
    HooksCheck().run(app)


def test_hooks_skips_paths_into_packages_that_are_not_bench_apps(tmp_path: Path) -> None:
    """Non-app packages are ImportCheck's job - the apps dir can't resolve them."""
    HooksCheck().run(_make_hooks_app(tmp_path, 'on_login = "some_pypi_package.hooks.on_login"\n'))


def test_hooks_skips_attribute_check_when_module_has_a_star_import(tmp_path: Path) -> None:
    app = _make_hooks_app(tmp_path, 'on_login = "myapp.utils.helper"\n', utils="from os.path import *\n")
    HooksCheck().run(app)


def test_hooks_resolves_symbol_defined_in_an_import_fallback(tmp_path: Path) -> None:
    """A hook target defined in an `except ImportError:` branch still imports."""
    app = _make_hooks_app(
        tmp_path,
        'after_migrate = "myapp.setup.after_migrate"\n',
        setup="try:\n"
        "    from vendor import after_migrate\n"
        "except ImportError:\n"
        "    def after_migrate():\n"
        "        pass\n",
    )
    HooksCheck().run(app)


def test_hooks_resolves_symbol_defined_behind_a_version_check(tmp_path: Path) -> None:
    app = _make_hooks_app(
        tmp_path,
        'on_login = "myapp.compat.on_login"\n',
        compat="import sys\n\nif sys.version_info >= (3, 11):\n    def on_login():\n        pass\n",
    )
    HooksCheck().run(app)


def test_hooks_reads_jenv_style_alias_paths(tmp_path: Path) -> None:
    app = _make_hooks_app(
        tmp_path, 'jinja = {"methods": ["shout:myapp.utils.shout"]}\n', utils="def whisper():\n    pass\n"
    )
    with pytest.raises(AppValidationError, match="'utils' has no 'shout'"):
        HooksCheck().run(app)
    (app.path / "myapp" / "utils.py").write_text("def shout():\n    pass\n")
    HooksCheck().run(app)


def _make_frappe_at(bench_root: Path, version: str) -> None:
    frappe_path = bench_root / "apps" / "frappe" / "frappe"
    frappe_path.mkdir(parents=True)
    (bench_root / "apps" / "frappe" / "pyproject.toml").write_text('[project]\nname = "frappe"\n')
    (frappe_path / "__init__.py").write_text(f'__version__ = "{version}"\n')


def _make_app_needing_frappe(bench_root: Path, specifier: str | None, name: str = "myapp") -> App:
    table = f"\n[tool.bench.frappe-dependencies]\nfrappe = {specifier!r}\n" if specifier else ""
    return _make_app(
        bench_root,
        name,
        f'[project]\nname = "{name}"\n{table}',
        {f"{name}/hooks.py": f"app_name = '{name}'\n"},
    )


def test_frappe_compatibility_passes_when_the_bench_is_in_range(tmp_path: Path) -> None:
    _make_frappe_at(tmp_path, "16.5.0")
    FrappeCompatibilityCheck().run(_make_app_needing_frappe(tmp_path, ">=16.0.0,<17.0.0"))


def test_frappe_compatibility_fails_when_the_bench_is_too_old(tmp_path: Path) -> None:
    """The migration break this catches: a new revision needs a newer frappe."""
    _make_frappe_at(tmp_path, "16.5.0")
    app = _make_app_needing_frappe(tmp_path, ">=17.0.0,<18.0.0")

    with pytest.raises(AppValidationError, match=r"needs frappe >=17\.0\.0,<18\.0\.0, but 16\.5\.0"):
        FrappeCompatibilityCheck().run(app)


def test_frappe_compatibility_counts_a_dev_build_as_a_prerelease(tmp_path: Path) -> None:
    """A bench on frappe develop reports 17.0.0-dev, which PEP 440 keeps out of
    '<17.0.0' - an app that says it stops at 16 does not silently run on 17."""
    _make_frappe_at(tmp_path, "17.0.0-dev")

    FrappeCompatibilityCheck().run(_make_app_needing_frappe(tmp_path, ">=16.0.0,<=17.0.0-dev"))

    stops_at_16 = _make_app_needing_frappe(tmp_path, ">=16.0.0,<17.0.0", name="otherapp")
    with pytest.raises(AppValidationError, match=r"17\.0\.0\.dev0 is installed"):
        FrappeCompatibilityCheck().run(stops_at_16)


def test_frappe_compatibility_leaves_an_app_that_declares_nothing_alone(tmp_path: Path) -> None:
    """This check runs on update, where an app may predate the table entirely."""
    _make_frappe_at(tmp_path, "16.5.0")
    FrappeCompatibilityCheck().run(_make_app_needing_frappe(tmp_path, None))


def test_frappe_compatibility_rejects_an_unreadable_range(tmp_path: Path) -> None:
    """Nothing else reads this table on update: VersionSpecifiersCheck covers
    [project], and DependencyDeclarationsCheck only runs on install."""
    _make_frappe_at(tmp_path, "16.5.0")
    app = _make_app_needing_frappe(tmp_path, ">=16.0.0 <17.0.0")  # missing comma

    with pytest.raises(AppValidationError, match="unreadable version for 'frappe'"):
        FrappeCompatibilityCheck().run(app)


def test_frappe_compatibility_ignores_an_app_that_is_not_installed(tmp_path: Path) -> None:
    """A missing required app is the dependency installer's error, not this one's."""
    _make_frappe_at(tmp_path, "16.5.0")
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n\n[tool.bench.frappe-dependencies]\n'
        'frappe = ">=16.0.0,<17.0.0"\nerpnext = ">=16.0.0,<17.0.0"\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},
    )

    FrappeCompatibilityCheck().run(app)


def test_fixtures_pass_when_every_file_parses(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/fixtures/role.json": '[{"doctype": "Role", "role_name": "Coach"}]\n',
        },
    )
    FixturesCheck().run(app)


def test_fixtures_fail_on_unparsable_json(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/fixtures/role.json": '[{"doctype": "Role"}]\n',
            "myapp/fixtures/custom_field.json": '[{\n"doctype": "Custom Field",\n',
        },
    )
    with pytest.raises(AppValidationError, match=r"myapp/fixtures/custom_field\.json"):
        FixturesCheck().run(app)


@pytest.mark.parametrize("doctype", ["DocType", "Page"])
def test_fixtures_fail_on_unimportable_doctypes(tmp_path: Path, doctype: str) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/fixtures/thing.json": f'[{{"doctype": "Role"}}, {{"doctype": "{doctype}"}}]\n',
        },
    )
    with pytest.raises(AppValidationError, match=f"contains '{doctype}' records"):
        FixturesCheck().run(app)


def test_fixtures_pass_when_the_app_has_no_fixtures(tmp_path: Path) -> None:
    app = _make_app(tmp_path, "myapp", '[project]\nname = "myapp"\n', {"myapp/hooks.py": ""})
    FixturesCheck().run(app)


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
    """Import resolution uses installed modules, not distribution names."""
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
    # files), not per-file - os/sys/json show up in the raw per-file parse.
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


def test_import_check_skips_imports_inside_functions(tmp_path: Path) -> None:
    app = _make_app(tmp_path, "myapp", '[project]\nname = "myapp"\n', {"myapp/hooks.py": ""})
    source = (
        "import required_dependency\n"
        "def lazy():\n"
        "    import definitely_missing_a\n"
        "    from definitely_missing_b import thing\n"
        "async def lazy_async():\n"
        "    import definitely_missing_c\n"
        "class Config:\n"
        "    import class_level_dependency\n"
        "    def method(self):\n"
        "        import definitely_missing_d\n"
    )
    assert _modules_for(app, "myapp/utils.py", source) == {
        "required_dependency",
        "class_level_dependency",
    }


def test_import_check_skips_type_checking_only_imports(tmp_path: Path) -> None:
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import only_needed_for_types\n"
        "import required_dependency\n"
    )
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {"myapp/hooks.py": "", "myapp/utils.py": source},
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
            "myapp/tests/helpers.py": "import dev_only_dependency\n",
            "myapp/module/tests/fixtures.py": "import dev_only_dependency\n",
        },
    )
    check = ImportCheck()
    assert check._imported_module_locations(app) == {}


def test_dependency_paths_covers_declared_apps_only(tmp_path: Path) -> None:
    """Installing every bench app instead would fail on apps that pin conflicting
    versions of a shared package - they coexist only because the real environment
    installs one app at a time."""
    _make_fake_frappe(tmp_path)
    _make_app(
        tmp_path,
        "erpnext",
        '[project]\nname = "erpnext"\nversion = "0.0.1"\n',
        {"erpnext/hooks.py": "app_name = 'erpnext'\n"},
    )
    _make_app(
        tmp_path,
        "unrelated",
        '[project]\nname = "unrelated"\nversion = "0.0.1"\n',
        {"unrelated/hooks.py": "app_name = 'unrelated'\n"},
    )
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\nversion = "0.0.1"\n\n'
        '[tool.bench.frappe-dependencies]\nfrappe = ">=16.0.0,<17.0.0"\nerpnext = ">=16.0.0,<17.0.0"\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},
    )

    paths = {p.name for p in ImportCheck._dependency_paths(app)}

    assert paths == {"erpnext"}  # frappe is installed first, the app itself last


def test_dependency_paths_tolerate_an_app_with_no_pyproject(tmp_path: Path) -> None:
    """ImportCheck also runs on update, where an app may predate pyproject.toml -
    it must not raise the declaration error that path deliberately skips."""
    app_path = tmp_path / "apps" / "oldapp"
    (app_path / "oldapp").mkdir(parents=True)
    (app_path / "oldapp" / "hooks.py").write_text("app_name = 'oldapp'\n")
    bench = _FakeBench(apps_path=tmp_path / "apps", env_path=tmp_path / "env")

    assert ImportCheck._dependency_paths(bench.app("oldapp")) == []


def test_import_check_trusts_the_bench_python_over_stat(monkeypatch, tmp_path: Path) -> None:
    """A package can bind submodules when imported (apiclient.discovery aliases a
    googleapiclient module), so no file exists to stat - asking the bench env
    keeps that from being reported as missing."""
    _make_fake_frappe(tmp_path)
    (tmp_path / "env" / "bin").mkdir(parents=True)
    (tmp_path / "env" / "bin" / "python").write_text("")
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/video.py": "from apiclient.discovery import build\n",
        },
    )

    probed: list[list[str]] = []
    monkeypatch.setattr(
        "pilot.core.app.validator.imports.unimportable_modules",
        lambda python, names: probed.append(names) or {},
    )
    monkeypatch.setattr(
        "pilot.core.app.validator.utils.tmp_env.TmpEnv.create",
        lambda *args, **kwargs: pytest.fail("the venv is not needed when the bench env has the module"),
    )

    ImportCheck().run(app)

    assert probed == [["apiclient.discovery"]]


def test_import_check_never_imports_the_app_it_is_validating(monkeypatch, tmp_path: Path) -> None:
    """Only third-party names go to the bench python; app modules stay stat-only."""
    _make_fake_frappe(tmp_path)
    (tmp_path / "env" / "bin").mkdir(parents=True)
    (tmp_path / "env" / "bin" / "python").write_text("")
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n", "myapp/utils.py": "from myapp.gone import helper\n"},
    )

    probed: list[list[str]] = []
    monkeypatch.setattr(
        "pilot.core.app.validator.imports.unimportable_modules",
        lambda python, names: probed.append(names) or {},
    )
    monkeypatch.setattr(
        "pilot.core.app.validator.utils.tmp_env.TmpEnv.create",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("venv path reached")),
    )

    with pytest.raises(RuntimeError, match="venv path reached"):
        ImportCheck().run(app)

    assert probed == []  # myapp.gone is the app's own module - never imported to check


def test_import_check_skips_the_throwaway_venv_when_everything_resolves(monkeypatch, tmp_path) -> None:
    """The expensive path only runs when the bench can't already satisfy an import."""
    _make_fake_frappe(tmp_path)
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "import frappe\nfrom myapp.hooks import app_name\n",
        },
    )

    def fail(*args, **kwargs):
        raise AssertionError("no venv should be created when imports resolve on disk")

    monkeypatch.setattr("pilot.core.app.validator.utils.tmp_env.TmpEnv.create", fail)
    ImportCheck().run(app)


def test_validation_env_installs_uv_when_the_host_has_none(monkeypatch, tmp_path: Path) -> None:
    """Validation must not fail just because uv isn't on PATH - it installs it,
    the same way the real app install does."""
    from pilot.core.app.validator.utils import tmp_env as tmp_env_module

    commands: list[list[str]] = []
    monkeypatch.setattr(tmp_env_module, "ensure_uv", lambda: "/installed/bin/uv")
    monkeypatch.setattr(tmp_env_module, "run_command", lambda argv, **kwargs: commands.append(argv))

    env = tmp_env_module.TmpEnv()
    env._dir = str(tmp_path)
    env._pip_install([tmp_path / "frappe"])

    assert commands[0][0] == "/installed/bin/uv"


def test_validation_env_installs_with_mysqlclient_build_flags(monkeypatch, tmp_path: Path) -> None:
    """The throwaway venv builds mysqlclient too, so it needs the same flags
    the bench env gets - without them uv fails on macOS ('Can not find valid
    pkg-config name')."""
    from pilot.core.app.validator.utils import tmp_env as tmp_env_module

    captured: dict = {}

    def fake_run_command(argv, **kwargs):
        captured["env"] = kwargs.get("env")

    monkeypatch.setattr(tmp_env_module, "run_command", fake_run_command)
    monkeypatch.setattr(
        tmp_env_module,
        "add_mysqlclient_flags",
        lambda env: env.setdefault("MYSQLCLIENT_CFLAGS", "-I/opt/mariadb/include"),
    )

    env = tmp_env_module.TmpEnv()
    env._dir = str(tmp_path)
    env._pip_install([tmp_path / "frappe"])

    assert captured["env"]["MYSQLCLIENT_CFLAGS"] == "-I/opt/mariadb/include"
    assert "PATH" in captured["env"]


def _app_with_symlink(tmp_path: Path, link_name: str, target: str) -> App:
    app = _make_app(
        tmp_path,
        "myapp",
        f'[project]\nname = "myapp"\nversion = "0.0.1"\n\n{_SETUPTOOLS_BUILD}',
        {"myapp/__init__.py": "", "myapp/hooks.py": "app_name = 'myapp'"},
    )
    link = app.path / link_name
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    return app


def test_symlink_check_rejects_a_broken_symlink(tmp_path: Path) -> None:
    """The frappe/wiki shape: an absolute link into another machine's home."""
    app = _app_with_symlink(
        tmp_path, "myapp/public/node_modules", "/Users/someone/benches/dec/apps/myapp/node_modules"
    )

    with pytest.raises(AppValidationError, match=r"do not resolve inside the app"):
        SymlinkCheck().run(app)


def test_symlink_check_reports_the_path_target_and_reason(tmp_path: Path) -> None:
    app = _app_with_symlink(tmp_path, "myapp/public/node_modules", "/nowhere/node_modules")

    with pytest.raises(AppValidationError) as exc:
        SymlinkCheck().run(app)

    message = str(exc.value)
    assert "myapp/public/node_modules -> /nowhere/node_modules (broken)" in message
    assert "myapp" in message


def test_symlink_check_allows_a_relative_link_inside_the_app(tmp_path: Path) -> None:
    """An in-repo link travels with the clone, so packaging it is fine."""
    app = _app_with_symlink(tmp_path, "myapp/public/shared", "../templates")
    (app.path / "myapp" / "templates").mkdir(parents=True)

    SymlinkCheck().run(app)  # no raise
    assert SymlinkCheck.get_invalid_symlinks(app.path) == []


def test_symlink_check_allows_an_absolute_link_inside_the_app(tmp_path: Path) -> None:
    app = _app_with_symlink(tmp_path, "myapp/vendor", str(tmp_path / "apps" / "myapp" / "myapp"))

    SymlinkCheck().run(app)  # no raise


def test_symlink_check_rejects_a_link_that_resolves_outside_the_app(tmp_path: Path) -> None:
    """It resolves here and would vanish on any other machine."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    app = _app_with_symlink(tmp_path, "myapp/vendor", str(outside))

    with pytest.raises(AppValidationError, match="points outside the app"):
        SymlinkCheck().run(app)


def test_symlink_check_rejects_an_in_app_link_that_hops_outside(tmp_path: Path) -> None:
    """Resolution follows the whole chain, not just the first hop."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    app = _app_with_symlink(tmp_path, "myapp/vendor", "./hop")
    (app.path / "myapp" / "hop").symlink_to(outside)

    with pytest.raises(AppValidationError, match="points outside the app"):
        SymlinkCheck().run(app)


def test_symlink_check_passes_for_an_app_without_symlinks(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        f'[project]\nname = "myapp"\nversion = "0.0.1"\n\n{_SETUPTOOLS_BUILD}',
        {"myapp/__init__.py": "", "myapp/hooks.py": "app_name = 'myapp'"},
    )

    SymlinkCheck().run(app)  # no raise


def test_symlink_check_ignores_git_and_node_modules_internals(tmp_path: Path) -> None:
    """Links npm and git create locally are not the app's committed content."""
    app = _make_app(
        tmp_path,
        "myapp",
        f'[project]\nname = "myapp"\nversion = "0.0.1"\n\n{_SETUPTOOLS_BUILD}',
        {"myapp/__init__.py": "", "myapp/hooks.py": "app_name = 'myapp'"},
    )
    for skipped in ("node_modules/.bin", ".git/annex"):
        directory = app.path / skipped
        directory.mkdir(parents=True)
        (directory / "linked").symlink_to("/nowhere")

    SymlinkCheck().run(app)  # no raise


def test_symlink_check_does_not_walk_through_an_allowed_symlinked_dir(tmp_path: Path) -> None:
    """An allowed link must not fall through to the directory branch: is_dir()
    follows links, so a link to its own parent would recurse until the OS
    refused and then report the valid link as broken."""
    app = _app_with_symlink(tmp_path, "myapp/loop", ".")

    SymlinkCheck().run(app)  # no raise, and terminates
    assert SymlinkCheck.get_invalid_symlinks(app.path) == []


def test_validation_ignore_skips_python_files_from_syntax_check(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n\n[tool.bench]\nvalidation-ignore = ["myapp/internal/*"]\n\n'
        '[tool.bench.frappe-dependencies]\nfrappe = ">=15"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/internal/generated.py": "def broken(:\n",
            "myapp/internal/nested/also_generated.py": "def broken(:\n",
        },
    )

    Validator(app, checks=_static_checks()).validate()  # no raise


def test_validation_ignore_does_not_skip_unmatched_files(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n\n[tool.bench]\nvalidation-ignore = ["myapp/internal/*"]\n\n'
        '[tool.bench.frappe-dependencies]\nfrappe = ">=15"\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n", "myapp/utils.py": "def broken(:\n"},
    )

    with pytest.raises(AppValidationError, match=r"syntax errors"):
        Validator(app, checks=_static_checks()).validate()


def test_validation_ignore_skips_fixtures(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n\n[tool.bench]\nvalidation-ignore = ["myapp/fixtures/*"]\n\n'
        '[tool.bench.frappe-dependencies]\nfrappe = ">=15"\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n", "myapp/fixtures/custom_field.json": "{not json"},
    )

    Validator(app, checks=_static_checks()).validate()  # no raise


def test_validation_ignore_skips_symlinks(tmp_path: Path) -> None:
    app = _app_with_symlink(tmp_path, "myapp/vendor/node_modules", "/nowhere/node_modules")
    (app.path / "pyproject.toml").write_text(
        f'[project]\nname = "myapp"\nversion = "0.0.1"\n\n'
        f'[tool.bench]\nvalidation-ignore = ["myapp/vendor/*"]\n\n{_SETUPTOOLS_BUILD}'
    )

    SymlinkCheck().run(app)  # no raise


def test_validation_ignore_rejects_a_non_list_value(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n\n[tool.bench]\nvalidation-ignore = "myapp/internal/*"\n\n'
        '[tool.bench.frappe-dependencies]\nfrappe = ">=15"\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},
    )

    with pytest.raises(AppValidationError, match=r"invalid \[tool\.bench\] validation-ignore"):
        Validator(app, checks=_static_checks()).validate()


def test_validation_rejects_a_non_table_bench_key(tmp_path: Path) -> None:
    """A non-table must fail as AppValidationError, not AttributeError: only a
    BenchError rolls a switched branch back."""
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n\n[tool]\nbench = "nope"\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},
    )

    with pytest.raises(AppValidationError, match=r"invalid \[tool\.bench\] in pyproject\.toml"):
        Validator(app, checks=_static_checks()).validate()


def test_validation_ignores_unrelated_tool_tables(tmp_path: Path) -> None:
    """Other tools' keys under [tool] are not pilot's to validate."""
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n\n[tool]\nx = ["sss"]\n\n'
        '[tool.ruff]\nline-length = 110\n\n[tool.bench.frappe-dependencies]\nfrappe = ">=15"\n',
        {"myapp/hooks.py": "app_name = 'myapp'\n"},
    )

    Validator(app, checks=_static_checks()).validate()  # no raise
