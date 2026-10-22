from __future__ import annotations

import re
import sys
import tomllib
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.managers.processes.definitions import ProcessDefinition, ProcessDefinitionBuilder

if TYPE_CHECKING:
    from pathlib import Path

    from pilot.core.app import App

# No hyphen, so `<app>-<name>` names one process of one app.
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]{0,31}$")
ENV_KEY_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
# Fixed rather than the caller's PATH, so a unit reads the same whoever writes it.
SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
if sys.platform == "darwin":
    SYSTEM_PATH = f"/opt/homebrew/bin:/opt/homebrew/sbin:{SYSTEM_PATH}"


class AppProcessError(BenchError):
    """An app declares a malformed process."""


class AppProcesses:
    """Processes an app declares in its pyproject.toml."""

    def __init__(self, app: "App") -> None:
        self.app = app

    @property
    def default_env(self) -> dict[str, str]:
        """The bench venv first on PATH, so `python` and the CLIs the app installs resolve to the bench's."""
        bench = self.app.bench
        return {
            **ProcessDefinitionBuilder.python_env(),
            "PATH": f"{bench.env_path / 'bin'}:{SYSTEM_PATH}",
            "VIRTUAL_ENV": str(bench.env_path),
            "FRAPPE_BENCH_ROOT": str(bench.path),
        }

    def get_definitions(self) -> list[ProcessDefinition]:
        return [
            self._get_definition(name, declaration) for name, declaration in self._read_declarations().items()
        ]

    def _read_declarations(self) -> dict:
        pyproject = self.app.path / "pyproject.toml"
        if not pyproject.exists():
            return {}

        try:
            data = tomllib.loads(pyproject.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise self._error(f"invalid pyproject.toml: {exc}") from exc

        declarations = data.get("tool", {}).get("pilot", {}).get("processes", {})
        if not isinstance(declarations, dict):
            raise self._error("[tool.pilot.processes] must be a table.")
        return declarations

    def _get_definition(self, name: str, declaration: object) -> ProcessDefinition:
        # Values come from a third-party app and end up in unit files, so reject anything unexpected.
        if not NAME_PATTERN.match(name):
            raise self._error(f"process name '{name}' must match {NAME_PATTERN.pattern}.")
        if not isinstance(declaration, dict):
            raise self._error(f"process '{name}' must be a table.")

        argv, working_dir = self._get_command(name, declaration)

        restart_on_failure = declaration.get("restart_on_failure", True)
        if not isinstance(restart_on_failure, bool):
            raise self._error(f"process '{name}' restart_on_failure must be true or false.")

        stop_timeout = declaration.get("stop_timeout")
        if stop_timeout is not None and not (type(stop_timeout) is int and stop_timeout >= 0):
            raise self._error(f"process '{name}' stop_timeout must be a non-negative integer.")

        full_name = f"{self.app.config.name}-{name}"
        return ProcessDefinition(
            name=full_name,
            argv=argv,
            log_file=self.app.bench.logs_path / f"{full_name}.log",
            env={**self.default_env, **self._get_env(name, declaration.get("env", {}))},
            working_dir=working_dir,
            stop_timeout=stop_timeout,
            critical=False,
            app=self.app.config.name,
            restart_on_failure=restart_on_failure,
            pre_run=self._get_argv(name, "pre_run", declaration, working_dir),
            post_run=self._get_argv(name, "post_run", declaration, working_dir),
        )

    def _get_command(self, name: str, declaration: dict) -> tuple[list[str], Path]:
        """argv and working directory from `command`, or from `frappe_command` run in the sites directory."""
        if ("command" in declaration) == ("frappe_command" in declaration):
            raise self._error(f"process '{name}' needs exactly one of command or frappe_command.")

        if "command" in declaration:
            working_dir = self.app.path / self._get_string(
                name, "working_dir", declaration.get("working_dir", ".")
            )
            return self._get_argv(name, "command", declaration, working_dir), working_dir

        if "working_dir" in declaration:
            raise self._error(f"process '{name}' frappe_command always runs in the sites directory.")
        sites_path = self.app.bench.sites_path
        arguments = self._get_argv(name, "frappe_command", declaration, sites_path)
        return [*self.app.bench.frappe_call, "frappe", *arguments], sites_path

    def _get_argv(self, name: str, field: str, declaration: dict, working_dir: Path) -> list[str]:
        value = declaration.get(field)
        if value is None:
            return []
        if not isinstance(value, list) or not value:
            raise self._error(f"process '{name}' {field} must be a non-empty list of strings.")

        argv = [self._get_string(name, field, argument) for argument in value]
        # Lexical only: pre_run may be what downloads the executable. A bare name is left for PATH.
        if "/" in argv[0]:
            argv[0] = str(working_dir / argv[0])
        return argv

    def _get_env(self, name: str, env: object) -> dict[str, str]:
        if not isinstance(env, dict):
            raise self._error(f"process '{name}' env must be a table.")
        values = {}
        for key, value in env.items():
            if not ENV_KEY_PATTERN.match(key):
                raise self._error(f"process '{name}' env key '{key}' must match {ENV_KEY_PATTERN.pattern}.")
            # A number such as PORT = 8080 is written as text; a bool has no single spelling.
            if type(value) in (int, float):
                value = str(value)
            values[key] = self._get_string(name, f"env.{key}", value)
        return values

    def _get_string(self, name: str, field: str, value: object) -> str:
        if not isinstance(value, str) or CONTROL_CHARACTER_PATTERN.search(value):
            raise self._error(f"process '{name}' {field} must be a string without control characters.")
        return value

    def _error(self, detail: str) -> AppProcessError:
        return AppProcessError(f"App '{self.app.config.name}': {detail}")
