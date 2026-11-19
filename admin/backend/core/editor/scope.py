from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from admin.backend.core.editor.workspace import EditorWorkspace
from pilot.core.bench import Bench


@dataclass(frozen=True)
class EditorScope:
    """The folder one editor request may use: an app or a site folder, below an optional base."""

    kind: str
    name: str
    root: Path
    base: str = ""
    excluded: tuple[str, ...] = ()

    # Secrets, backups, uploads that File records point to, and runtime files.
    site_excluded = ("site_config.json", "private/backups", "private/files", "public/files", "logs", "locks")

    @classmethod
    def for_app(cls, bench: Bench, name: str, base: str = "") -> EditorScope:
        """An installed app's folder; raises BenchError for an unknown app."""
        return cls("app", name, bench.app(name).path, base)

    @classmethod
    def for_site(cls, bench: Bench, name: str, base: str = "") -> EditorScope:
        """A site's own folder, without its excluded paths."""
        return cls("site", name, bench.site(name).path, base, cls.site_excluded)

    @property
    def state_key(self) -> str:
        """The file name of this scope's saved editor state."""
        if self.kind == "app" and not self.base:
            return self.name
        digest = hashlib.sha256(self.base.encode()).hexdigest()[:12]
        return f"{self.kind}-{self.name}-{digest}"

    def get_workspace(self) -> EditorWorkspace:
        """The workspace rooted at the base; raises when the base escapes the scope or is not a folder."""
        excluded = tuple(self.root / path for path in self.excluded)
        path = EditorWorkspace(self.root, has_git=False, excluded=excluded).safe(self.base)
        if not path.is_dir():
            raise NotADirectoryError(self.base) if path.exists() else FileNotFoundError(self.base)
        return EditorWorkspace(path, state_key=self.state_key, has_git=self.kind == "app", excluded=excluded)
