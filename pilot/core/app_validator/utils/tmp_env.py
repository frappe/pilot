from __future__ import annotations

import json
import shutil
import tempfile
import typing
from collections.abc import Iterable
from pathlib import Path

from pilot.exceptions import AppValidationError, BenchError, CommandError
from pilot.utils import run_command

if typing.TYPE_CHECKING:
    from pilot.core.app import App


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

    def create(self, frappe_path: Path) -> "TmpEnv":
        self._dir = tempfile.mkdtemp(prefix="pilot-app-validate-")
        try:
            run_command([self._uv(), "venv", str(self.path)], stream_output=True)
        except CommandError as exc:
            raise AppValidationError(
                f"Failed to create temporary environment for validation:\n{exc.message}"
            )
        try:
            self._pip_install([frappe_path])
        except CommandError as exc:
            raise AppValidationError(
                f"Failed to install frappe into the validation env:\n{exc.message}"
            )
        return self

    def install_app(self, app: "App", dependency_paths: Iterable[Path] = ()) -> None:
        # Installed together so imports across the app and its bench-installed
        # required apps (e.g. erpnext) resolve in one shot.
        try:
            self._pip_install([*dependency_paths, app.path])
        except CommandError as exc:
            raise AppValidationError(f"'{app.config.name}' failed to install:\n{exc.message}")

    def resolve_modules(self, module_names: list[str]) -> dict[str, str]:
        """Return {module: reason} for names that don't resolve via find_spec
        (no code runs — this just confirms what the stat-based check found)."""
        try:
            result = run_command(
                [str(self.path / "bin" / "python"), "-c", self._resolve_check_script(module_names)]
            )
        except CommandError as exc:
            raise AppValidationError(f"Failed to check imports:\n{exc.message}")
        return json.loads(result.stdout)

    @staticmethod
    def _resolve_check_script(module_names: list[str]) -> str:
        return (
            "import importlib.util, json\n"
            "errors = {}\n"
            f"for name in {module_names!r}:\n"
            "    try:\n"
            "        if importlib.util.find_spec(name) is None:\n"
            "            raise ModuleNotFoundError(f'No module named {name!r}')\n"
            "    except Exception as exc:\n"
            "        errors[name] = str(exc)\n"
            "print(json.dumps(errors))\n"
        )

    def _pip_install(self, paths: list[Path]) -> None:
        python = str(self.path / "bin" / "python")
        run_command([self._uv(), "pip", "install", "--python", python, *map(str, paths)])

    def delete(self) -> None:
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None

    @staticmethod
    def _uv() -> str:
        uv = shutil.which("uv")
        if not uv:
            raise BenchError("uv not found — run the pilot install script to set it up")
        return uv
