from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.managers.processes.definitions import ProcessDefinition

if TYPE_CHECKING:
    from pilot.core.app import App

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class AppRequirementsError(BenchError):
    """An app's [tool.pilot] declaration is malformed or unsafe."""


class AppRequirements:
    """An app's [tool.pilot.background_processes] declarations, from its pyproject.toml.

    Every value comes from a third-party app, so each field is rejected - never
    sanitized - before it can reach a unit file or a shell. See docs/app-processes.md.
    """

    def __init__(self, app: "App") -> None:
        self.app = app

    def process_definitions(self) -> list[ProcessDefinition]:
        return [self._build_definition(name, entry) for name, entry in self._declarations.items()]

    @property
    def _declarations(self) -> dict:
        pyproject = self.app.path / "pyproject.toml"
        if not pyproject.exists():
            return {}
        try:
            data = tomllib.loads(pyproject.read_text())
        except (tomllib.TOMLDecodeError, OSError) as exc:
            raise self._error(f"unreadable pyproject.toml: {exc}") from exc
        tool_pilot = data.get("tool", {}).get("pilot", {})
        entries = tool_pilot.get("background_processes", {}) if isinstance(tool_pilot, dict) else {}
        if not isinstance(entries, dict):
            raise self._error("[tool.pilot.background_processes] must be a table of tables.")
        return entries

    def _build_definition(self, name: str, entry: object) -> ProcessDefinition:
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise self._error(f"process name '{name}' is invalid; must match {_NAME_RE.pattern}.")
        if not isinstance(entry, dict):
            raise self._error(f"process '{name}' must be a table.")

        working_dir = self._working_dir(name, entry.get("working_dir"))

        cmd = self._argv(name, "cmd", entry.get("cmd"), working_dir, required=True)
        pre_run = self._argv(name, "pre_run", entry.get("pre_run"), working_dir)
        post_run = self._argv(name, "post_run", entry.get("post_run"), working_dir)

        restart_on_failure = entry.get("restart_on_failure", True)
        if not isinstance(restart_on_failure, bool):
            raise self._error(f"process '{name}' restart_on_failure must be true or false.")

        env = self._env(name, entry.get("env", {}))

        stop_timeout = entry.get("stop_timeout")
        if stop_timeout is not None and (not isinstance(stop_timeout, int) or stop_timeout < 0):
            raise self._error(f"process '{name}' stop_timeout must be a non-negative integer.")

        prefixed = f"{self.app.config.name}-{name}"
        return ProcessDefinition(
            name=prefixed,
            argv=cmd,
            log_file=self.app.bench.logs_path / f"{prefixed}.log",
            env=env,
            working_dir=working_dir,
            stop_timeout=stop_timeout,
            # An app-declared process crashing must not take down the whole bench -
            # only the bench's own core processes (web, workers, redis) are critical.
            critical=False,
            restart_on_failure=restart_on_failure,
            pre_run=pre_run,
            post_run=post_run,
        )

    def _working_dir(self, name: str, value: object) -> Path:
        """Defaults to the app directory; a relative path is read from there. Each
        manager has its own default, so an unnamed one would start unpredictably."""
        if value is None:
            return self.app.path
        self._reject_control(name, "working_dir", value)
        path = Path(str(value))
        return path if path.is_absolute() else self.app.path / path

    def _env(self, name: str, env: object) -> dict[str, str]:
        if not isinstance(env, dict):
            raise self._error(f"process '{name}' env must be a table.")
        for key, value in env.items():
            if not _ENV_KEY_RE.match(key):
                raise self._error(
                    f"process '{name}' env key '{key}' is invalid; must match {_ENV_KEY_RE.pattern}."
                )
            self._reject_control(name, f"env[{key}]", value)
        return {key: str(value) for key, value in env.items()}

    def _argv(
        self, name: str, field: str, value: object, working_dir: Path, required: bool = False
    ) -> list[str]:
        """An argv list - executable plus args, never a shell string."""
        if value is None:
            if required:
                raise self._error(f"process '{name}' needs a non-empty {field} list.")
            return []
        if not isinstance(value, list) or not value:
            raise self._error(f"process '{name}' {field} must be a non-empty list of strings.")
        for arg in value:
            self._reject_control(name, field, arg)
        argv = [str(arg) for arg in value]
        argv[0] = self._resolve_executable(argv[0], working_dir)
        return argv

    @staticmethod
    def _resolve_executable(executable: str, working_dir: Path) -> str:
        """Anchor a path-like executable to the working directory, lexically - a
        `pre_run` hook may be what downloads it. A bare name is left for PATH."""
        if "/" not in executable:
            return executable
        return executable if executable.startswith("/") else str(working_dir / executable)

    def _reject_control(self, process: str, field: str, value: object) -> None:
        if not isinstance(value, str) or _CONTROL_RE.search(value):
            raise self._error(
                f"process '{process}' field '{field}' must be a string with no control characters."
            )

    def _error(self, detail: str) -> AppRequirementsError:
        return AppRequirementsError(f"'{self.app.config.name}': {detail}")
