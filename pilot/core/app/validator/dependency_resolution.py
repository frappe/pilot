from __future__ import annotations

import os
import re
import tempfile
import typing
from pathlib import Path

from pilot._vendor.packaging.requirements import InvalidRequirement, Requirement
from pilot.core.app.validator.base import read_pyproject
from pilot.exceptions import AppValidationError, CommandError
from pilot.internal.command import run_command
from pilot.managers.environment import ensure_uv
from pilot.managers.platform import HostPlatform

if typing.TYPE_CHECKING:
    from pilot.core.app import App


class DependencyResolutionCheck:
    """Resolve the app's dependencies against the bench, without installing.

    Runs the install command with --dry-run, constrained by what every other app
    on the bench requires. Apps share one environment, so a package can only be
    installed at one version: without the constraints uv is free to move a shared
    package and silently break an app that pinned it.
    """

    def run(self, app: "App") -> None:
        """Raise when the app's requirements conflict with the bench environment."""
        python = app.bench.env_path / "bin" / "python"
        if not python.exists():
            return  # no environment to resolve against yet

        environment = os.environ.copy()
        HostPlatform().add_mysqlclient_flags(environment)
        with tempfile.TemporaryDirectory(prefix="pilot-constraints-") as directory:
            constraints = self._write_constraints(app, Path(directory))
            argv = [ensure_uv(), "pip", "install", "--dry-run", "--python", str(python)]
            if constraints:
                argv += ["--constraint", str(constraints)]
            try:
                run_command([*argv, "-e", str(app.path)], env=environment)
            except CommandError as exc:
                raise AppValidationError(self._failure(app, constraints, exc)) from exc

    @staticmethod
    def _write_constraints(app: "App", directory: Path) -> Path | None:
        """The other apps' requirements as a constraints file, one app per comment."""
        lines = []
        for installed in app.bench.apps():
            if installed.config.name == app.config.name:
                continue
            for requirement in _declared_requirements(installed):
                lines.append(f"{requirement}  # {installed.config.name}")
        if not lines:
            return None
        path = directory / "constraints.txt"
        path.write_text("\n".join(sorted(set(lines))) + "\n")
        return path

    @staticmethod
    def _failure(app: "App", constraints: Path | None, exc: CommandError) -> str:
        """uv's explanation, plus which app asked for the packages it named."""
        blamed = []
        if constraints:
            blamed = [
                line
                for line in constraints.read_text().splitlines()
                if _is_about(_package_of(line), exc.message)
            ]
        message = (
            f"'{app.config.name}' has dependencies that can't be resolved against this bench:\n"
            f"{_explanation(exc.message)}\n"
        )
        if blamed:
            message += "Already required by:\n" + "\n".join(f"  {line}" for line in blamed) + "\n"
        return message + (
            f"Widen the requirement in {app.config.name}'s pyproject.toml, or update the app "
            "that pinned the version it clashes with."
        )


def _declared_requirements(app: "App") -> list[str]:
    """`[project].dependencies` that make sense as constraints - no extras.

    URL requirements are kept: uv refuses to resolve any tree containing one
    unless it is pinned as a direct requirement or a constraint, and frappe pins
    two. Dropping them fails every app that depends on frappe.
    """
    try:
        data = read_pyproject(app) or {}
    except AppValidationError:
        # Only ever called on the other apps, whose broken pyproject.toml is
        # their own checks' business - it must not block this install.
        return []
    requirements = []
    for entry in data.get("project", {}).get("dependencies", []):
        try:
            requirement = Requirement(entry)
        except (InvalidRequirement, TypeError):
            continue
        if requirement.extras:
            continue  # a constraints file may not carry extras
        marker = f" ; {requirement.marker}" if requirement.marker else ""
        if requirement.url:
            requirements.append(f"{requirement.name} @ {requirement.url}{marker}")
        elif str(requirement.specifier):
            requirements.append(f"{requirement.name}{requirement.specifier}{marker}")
    return requirements


def _explanation(command_error: str) -> str:
    """uv's reasoning, without the two lines that explain nothing: our own
    subprocess wrapper's preamble, and uv naming the environment it used.
    """
    noise = ("Command ", "Using Python ")
    return "\n".join(line for line in command_error.splitlines() if not line.startswith(noise)).strip()


def _package_of(constraint_line: str) -> str:
    """The package name of a constraint line."""
    # rsplit on the exact separator _write_constraints uses: a URL may carry its
    # own '#egg=' fragment, and splitting on that would truncate it.
    return Requirement(constraint_line.rsplit("  # ", 1)[0].strip()).name


def _is_about(package: str, message: str) -> bool:
    """Whether uv's message is about this package, and not a word containing it."""
    return re.search(rf"(?<![\w.-]){re.escape(package)}(?![\w.-])", message, re.IGNORECASE) is not None
