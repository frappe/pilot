from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.internal.atomic_file import (
    OVERWRITE_JOURNAL,
    exclusive_file_lock,
    finish_overwrite,
    overwrite_private_text_locked,
)
from pilot.internal.validators import validate_app_name

if TYPE_CHECKING:
    from pilot.core.bench import Bench

MAX_NAME_LENGTH = 50


class InvalidSiteTemplateError(BenchError):
    """Raised when a site template has an invalid name or app list."""


class SiteTemplateNotFoundError(BenchError):
    """Raised when a bench has no site template with the given name."""


@dataclass
class SiteTemplate:
    """A named app selection offered when a site is created."""

    name: str
    apps: list[str] = field(default_factory=list)

    def validate(self) -> None:
        """Raise when the name or the app list is invalid."""
        name = self.name
        if not name or name != name.strip():
            raise InvalidSiteTemplateError("A site template needs a name without leading or trailing spaces.")
        if len(name) > MAX_NAME_LENGTH:
            raise InvalidSiteTemplateError(
                f"Site template name must be at most {MAX_NAME_LENGTH} characters."
            )
        if "/" in name or not name.isprintable():
            raise InvalidSiteTemplateError("Site template name must not contain '/' or control characters.")
        if not self.apps:
            raise InvalidSiteTemplateError(f"Site template '{name}' needs at least one app.")
        if len(set(self.apps)) != len(self.apps):
            raise InvalidSiteTemplateError(f"Site template '{name}' lists an app twice.")
        for app in self.apps:
            if error := validate_app_name(app):
                raise InvalidSiteTemplateError(f"Site template '{name}': {error}")


class SiteTemplates:
    """The app selections a bench saves for new sites, kept in one JSON file."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    @property
    def path(self) -> Path:
        """The site templates file."""
        return self.bench.config_path / "site-templates.json"

    @property
    def all(self) -> list[SiteTemplate]:
        """The saved templates, in the order they were added."""
        try:
            entries = json.loads(self.path.read_text())
        except FileNotFoundError:
            return []
        return [SiteTemplate(**entry) for entry in entries]

    def save(self, name: str, apps: list[str]) -> SiteTemplate:
        """Add the template, or replace the one with the same name, ignoring case."""
        template = SiteTemplate(name, apps)
        template.validate()
        with self.open() as templates:
            index = self.get_index(templates, name)
            if index is None:
                templates.append(template)
            else:
                templates[index] = template
        return template

    def remove(self, name: str) -> None:
        """Remove the template with the name, ignoring case."""
        with self.open() as templates:
            index = self.get_index(templates, name)
            if index is None:
                raise SiteTemplateNotFoundError(f"Site template '{name}' was not found.")
            del templates[index]

    @contextmanager
    def open(self) -> Iterator[list[SiteTemplate]]:
        """Lock the file, yield its templates, and write back what the caller leaves in the list.

        Written in place, so a sandboxed unit's read-only bind stays."""
        journal = self.bench.path / OVERWRITE_JOURNAL
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path):
            finish_overwrite(self.path, journal)
            templates = self.all
            yield templates
            content = json.dumps([asdict(template) for template in templates], indent=2)
            overwrite_private_text_locked(self.path, content, journal)

    @staticmethod
    def get_index(templates: list[SiteTemplate], name: str) -> int | None:
        """Where the template with the name is, ignoring case."""
        folded = name.casefold()
        return next((index for index, item in enumerate(templates) if item.name.casefold() == folded), None)
