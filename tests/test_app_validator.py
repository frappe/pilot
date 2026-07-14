"""Tests for Validator's pre-install static checks on a cloned app."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from pilot.config.app_config import AppConfig
from pilot.core.app import App
from pilot.core.app_validator import Validator
from pilot.exceptions import AppValidationError


@dataclass
class _FakeBench:
    apps_path: Path
    env_path: Path

    @property
    def python(self) -> Path:
        return self.env_path / "bin" / "python"


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


def _stub_bench_python(monkeypatch: pytest.MonkeyPatch, app: App, import_to_distribution: dict[str, list[str]]) -> None:
    """Simulate the bench env's `packages_distributions()` output without spawning it."""
    import json
    from subprocess import CompletedProcess

    app.bench.python.parent.mkdir(parents=True, exist_ok=True)
    app.bench.python.touch()
    monkeypatch.setattr(
        "pilot.core.app_validator.subprocess.run",
        lambda *a, **k: CompletedProcess(args=[], returncode=0, stdout=json.dumps(import_to_distribution)),
    )


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
    Validator(app).validate()


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


def test_validate_internal_imports_fails_on_broken_reference(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "from myapp.missing_module import thing\n",
        },
    )
    with pytest.raises(AppValidationError, match="broken internal imports"):
        Validator(app).validate()


def test_validate_external_imports_fails_on_undeclared_dependency(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "import numpy\n",
        },
    )
    with pytest.raises(AppValidationError, match="numpy"):
        Validator(app).validate()


def test_validate_passes_with_stdlib_and_frappe_imports(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "import os\nimport json\nimport frappe\n",
        },
    )
    Validator(app).validate()


def test_validate_passes_for_submodule_dependency_name(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\ndependencies = ["Pillow>=9"]\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "from PIL import Image\n",
        },
    )
    with pytest.raises(AppValidationError, match="PIL"):
        Validator(app).validate()


def test_validate_internal_imports_passes_for_resolvable_package_import(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/sub/__init__.py": "",
            "myapp/utils.py": "import myapp.sub\n",
        },
    )
    Validator(app).validate()


def test_validate_internal_imports_ignores_relative_imports(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/sub/__init__.py": "",
            "myapp/sub/utils.py": "from . import missing\n",
        },
    )
    Validator(app).validate()


def test_validate_passes_for_dependency_declared_by_frappe(tmp_path: Path) -> None:
    _make_app(
        tmp_path,
        "frappe",
        '[project]\nname = "frappe"\ndependencies = ["bleach>=6"]\n',
        {"frappe/hooks.py": "app_name = 'frappe'\n"},
    )
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "import bleach\n",
        },
    )
    Validator(app).validate()


def test_validate_passes_for_import_name_that_differs_from_distribution_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\ndependencies = ["beautifulsoup4>=4"]\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "from bs4 import BeautifulSoup\n",
        },
    )
    _stub_bench_python(monkeypatch, app, {"bs4": ["beautifulsoup4"]})
    Validator(app).validate()


def test_validate_fails_for_import_name_mismatch_when_not_installed_in_bench_env(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\ndependencies = ["beautifulsoup4>=4"]\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "from bs4 import BeautifulSoup\n",
        },
    )
    with pytest.raises(AppValidationError, match="bs4"):
        Validator(app).validate()


def test_validate_ignores_undeclared_import_guarded_by_import_error(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "try:\n    import numpy\nexcept ImportError:\n    numpy = None\n",
        },
    )
    Validator(app).validate()


def test_validate_ignores_broken_internal_import_guarded_by_import_error(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "try:\n    from myapp.missing_module import thing\nexcept ImportError:\n    thing = None\n",
        },
    )
    Validator(app).validate()


def test_validate_fails_for_broken_import_in_except_handler(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": (
                "try:\n    import ujson as json\nexcept ImportError:\n    from myapp.missing_module import json\n"
            ),
        },
    )
    with pytest.raises(AppValidationError, match="broken internal imports"):
        Validator(app).validate()


def test_validate_fails_for_undeclared_import_outside_try_block(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "try:\n    import numpy\nexcept ImportError:\n    numpy = None\nimport pandas\n",
        },
    )
    with pytest.raises(AppValidationError) as exc_info:
        Validator(app).validate()
    assert "pandas" in str(exc_info.value)
    assert "numpy" not in str(exc_info.value)


def test_validate_external_imports_reports_all_missing_dependencies(tmp_path: Path) -> None:
    app = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\n',
        {
            "myapp/hooks.py": "app_name = 'myapp'\n",
            "myapp/utils.py": "import numpy\nimport pandas\n",
        },
    )
    with pytest.raises(AppValidationError, match="numpy, pandas"):
        Validator(app).validate()
