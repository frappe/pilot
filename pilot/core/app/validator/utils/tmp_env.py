from __future__ import annotations

import json
import os
import shutil
import tempfile
import typing
from collections.abc import Iterable
from pathlib import Path

from pilot.exceptions import AppValidationError, BenchError, CommandError
from pilot.managers.environment import ensure_uv
from pilot.managers.platform import add_mysqlclient_flags
from pilot.utils import run_command

if typing.TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.bench import Bench


class TmpEnv:
    """A throwaway venv an app is installed into, to validate the install
    succeeds before it touches the bench's real environment."""

    def __init__(self) -> None:
        self._dir: str | None = None

    @property
    def path(self) -> Path:
        if self._dir is None:
            raise BenchError("Temporary environment not created yet.")
        return Path(self._dir)

    def create(self, bench: "Bench") -> "TmpEnv":
        """Built on the bench's interpreter, so source the bench can compile does
        not fail to build here."""
        self._dir = tempfile.mkdtemp(prefix="pilot-app-validate-")
        try:
            run_command(
                [ensure_uv(), "venv", "--python", str(bench.python), str(self.path)],
                stream_output=True,
            )
        except CommandError as exc:
            raise AppValidationError(
                f"Failed to create temporary environment for validation:\n{exc.message}"
            ) from exc
        try:
            self._pip_install([bench.apps_path / "frappe"])
        except CommandError as exc:
            raise AppValidationError(
                f"Failed to install frappe into the validation env:\n{exc.message}"
            ) from exc
        return self

    def install_app(self, app: "App", dependency_paths: Iterable[Path] = ()) -> None:
        # Installed together so imports across the app and its bench-installed
        # required apps (e.g. erpnext) resolve in one shot.
        try:
            self._pip_install([*dependency_paths, app.path])
        except CommandError as exc:
            raise AppValidationError(f"'{app.config.name}' failed to install:\n{exc.message}") from exc

    @property
    def python(self) -> Path:
        return self.path / "bin" / "python"

    def _pip_install(self, paths: list[Path]) -> None:
        python = str(self.python)
        env = os.environ.copy()
        add_mysqlclient_flags(env)
        run_command([ensure_uv(), "pip", "install", "--python", python, *map(str, paths)], env=env)

    def delete(self) -> None:
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None


_FIND_SPEC_SCRIPT = """
import importlib.util, json
errors = {}
for name in NAMES:
    try:
        if importlib.util.find_spec(name) is None:
            raise ModuleNotFoundError(f'No module named {name!r}')
    except Exception as exc:
        errors[name] = str(exc)
print(json.dumps(errors))
"""

_IMPORT_SCRIPT = """
import importlib, json
errors = {}
for name in NAMES:
    try:
        importlib.import_module(name)
    except Exception as exc:
        errors[name] = str(exc)
print(json.dumps(errors))
"""


def missing_modules(python: Path, module_names: list[str]) -> dict[str, str]:
    """{module: reason} for names `python` can't resolve, without importing them."""
    return _probe(python, module_names, _FIND_SPEC_SCRIPT)


def unimportable_modules(python: Path, module_names: list[str]) -> dict[str, str]:
    """{module: reason} for names `python` can't import.

    This one imports, so keep it to third-party packages the bench already has:
    a package can bind submodules as attributes when imported - `apiclient.discovery`
    aliases a googleapiclient module - and find_spec reports those as missing even
    though the import works.
    """
    return _probe(python, module_names, _IMPORT_SCRIPT)


def _probe(python: Path, module_names: list[str], script: str) -> dict[str, str]:
    try:
        result = run_command([str(python), "-c", script.replace("NAMES", repr(module_names))])
    except CommandError as exc:
        raise AppValidationError(f"Failed to check imports:\n{exc.message}") from exc
    return json.loads(result.stdout)
