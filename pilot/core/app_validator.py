from __future__ import annotations

import ast
import shutil
import tempfile
import typing
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

    def create(self, frappe_path: Path) -> TmpEnv:
        self._dir = tempfile.mkdtemp(prefix="pilot-app-validate-")
        run_command([self._uv(), "venv", str(self.path)], stream_output=True)
        try:
            self._pip_install(str(self.path / "bin" / "python"), frappe_path)
        except CommandError as exc:
            raise AppValidationError(
                f"Failed to install frappe into the validation env:\n{exc.message}"
            )
        return self

    def install_app(self, app: "App") -> None:
        try:
            self._pip_install(str(self.path / "bin" / "python"), app.path)
        except CommandError as exc:
            raise AppValidationError(f"'{app.config.name}' failed to install:\n{exc.message}")

    def _pip_install(self, python: str, path: Path) -> None:
        run_command([self._uv(), "pip", "install", "--python", python, str(path)])

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


class Validator:
    """Statically validates a cloned app before it's installed into the bench
    env, so a broken app is rejected up front instead of after `pip install`
    has already touched the environment."""

    def __init__(self, app: "App"):
        self.app = app
        self.module_path = app.path / app.module_name
        self.tmp_env = self.create_tmp_env_with_frappe_app()

    def validate(self) -> None:
        self.validate_repo_structure()
        self.validate_syntax()
        try:
            self.tmp_env.install_app(self.app)
        finally:
            self.tmp_env.delete()

    def create_tmp_env_with_frappe_app(self) -> TmpEnv:
        return TmpEnv().create(self.app.bench.apps_path / "frappe")

    def validate_repo_structure(self) -> None:
        if not (self.app.path / "pyproject.toml").exists():
            raise AppValidationError(f"'{self.app.config.name}' has no pyproject.toml.")
        if not self.module_path.is_dir():
            raise AppValidationError(
                f"'{self.app.config.name}' has no '{self.app.module_name}' package directory."
            )
        if not (self.module_path / "hooks.py").exists():
            raise AppValidationError(
                f"'{self.app.config.name}' is missing {self.app.module_name}/hooks.py."
            )

    def validate_syntax(self) -> None:
        broken = [
            f"{path.relative_to(self.app.path)}: {error}"
            for path in self._python_files()
            for error in self._syntax_errors(path)
        ]
        if broken:
            raise AppValidationError(
                f"'{self.app.config.name}' has Python syntax errors:\n"
                + "\n".join(f"  {b}" for b in broken)
            )

    @staticmethod
    def _syntax_errors(path: Path) -> list[str]:
        try:
            ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:
            return [f"line {exc.lineno}: {exc.msg}"]
        except OSError:
            return []
        return []

    def _python_files(self) -> list[Path]:
        return list(self.module_path.rglob("*.py"))
